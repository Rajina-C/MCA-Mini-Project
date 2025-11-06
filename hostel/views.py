# hostel/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, F, Prefetch, Sum, Q
from django.contrib.auth.models import User
from django.contrib import messages
from .forms import SimpleRegistrationForm, StudentApplicationForm, ProfileUpdateForm
from .models import StudentApplication, Room, Hostel
from django.http import HttpResponse
from django.template.loader import get_template
from xhtml2pdf import pisa
from io import BytesIO

# --- Home View ---
# Redirects logged-in users to dashboard, shows welcome page otherwise
def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'hostel/home.html')

# --- Registration View ---
# Uses SimpleRegistrationForm (username, email, pass, gender, level)
def register(request):
    if request.user.is_authenticated:
         return redirect('dashboard') # Send logged-in users to dashboard

    if request.method == 'POST':
        form = SimpleRegistrationForm(request.POST)
        if form.is_valid():
            # Create the User
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password']
            )
            # Create the initial "incomplete" StudentApplication
            application = StudentApplication(
                student=user,
                gender=form.cleaned_data['gender'],
                level=form.cleaned_data['level']
                # Other fields (address, docs, photo) remain null/blank initially
            )
            application.save()
            # Log the user in
            login(request, user)
            # Send to dashboard to complete the application
            return redirect('dashboard')
    else: # GET request
        form = SimpleRegistrationForm()

    return render(request, 'hostel/register.html', {'form': form})

# --- Student Dashboard View ---
# Shows application status if complete, or form to complete it otherwise
@login_required
def student_dashboard(request):
    # Redirect Admins and Wardens away from student dashboard
    if request.user.is_superuser or request.user.is_staff:
        return redirect('/admin/')

    # Get the student's application or show 404 if somehow missing
    application = get_object_or_404(StudentApplication, student=request.user)

    # Check if application is "complete" (i.e., provisional letter is uploaded)
    # This check now also correctly handles re-application
    if application.provisional_letter:
        # If complete, show status (Pending, Approved, or Rejected)
        return render(request, 'hostel/dashboard.html', {'application': application, 'form': None})
    else:
        # If incomplete (or reset by re-apply), show the form
        if request.method == 'POST':
            form = StudentApplicationForm(request.POST, request.FILES, instance=application)
            if form.is_valid():
                form.save()
                messages.success(request, 'Your application has been submitted successfully!')
                return redirect('dashboard')
        else:
            form = StudentApplicationForm(instance=application)

        # Render the dashboard, passing the form to show it
        return render(request, 'hostel/dashboard.html', {'application': application, 'form': form})

# --- Admin/Warden Assign Room View (Includes Priority Logic) ---
@staff_member_required
def assign_room(request, application_id):
    application = get_object_or_404(StudentApplication, id=application_id)
    target_hostel = None # The hostel that should currently be accepting students

    # Find the highest priority, non-full hostel matching the student's category
    candidate_hostels = Hostel.objects.filter(
        gender=application.gender,
        level=application.level
    ).order_by('priority')

    for hostel in candidate_hostels:
        if not hostel.is_full(): # Use the is_full() method from the Hostel model
            target_hostel = hostel
            break # Found the first available hostel in priority order

    available_rooms = Room.objects.none() # Default to no rooms available/shown
    if target_hostel:
        # If a target hostel was found, get its available rooms
        available_rooms = Room.objects.filter(
            hostel=target_hostel
        ).annotate(
            num_students=Count('studentapplication')
        ).filter(
            num_students__lt=F('capacity') # Only rooms not yet full
        )

    # Further restrict rooms if the user is a Warden (not Admin)
    if not request.user.is_superuser and request.user.groups.filter(name='Warden').exists():
        managed_hostel_ids = request.user.managed_hostels.values_list('id', flat=True)
        # Check if the determined target hostel is actually managed by this warden
        if target_hostel and target_hostel.id not in managed_hostel_ids:
            target_hostel = None # Warden cannot assign to this hostel
            available_rooms = Room.objects.none()
            messages.warning(request, f"The current priority hostel ({candidate_hostels.first().name if candidate_hostels.exists() else 'N/A'}) is not managed by you.")
        elif target_hostel:
            # Re-filter available_rooms (technically redundant but safe)
            available_rooms = available_rooms.filter(hostel__id=target_hostel.id)

    # Handle form submission
    if request.method == 'POST':
        room_id = request.POST.get('room_id')
        # Check if a target hostel exists, a room was selected, and it's a valid choice
        if room_id and target_hostel and available_rooms.filter(id=room_id).exists():
            room = get_object_or_404(available_rooms, id=room_id)

            # Final authorization check: Superuser or Warden managing this specific room's hostel
            if not request.user.is_superuser and room.hostel not in request.user.managed_hostels.all():
                 messages.error(request, "Authorization error: You cannot assign rooms in this hostel.")
                 return redirect('assign_room', application_id=application_id)

            # Assign room and save
            application.assigned_room = room
            application.save()
            messages.success(request, f"Room {room.room_number} in {room.hostel.name} assigned successfully to {application.student.username}.")
            # Redirect back to the admin application list for workflow continuity
            redirect_url = '/admin/hostel/studentapplication/'
            return redirect(redirect_url)

        # Handle error cases for POST
        elif not target_hostel:
             messages.error(request, "Cannot assign room: No suitable hostel is currently available according to priority, or you are not assigned to manage it.")
        elif not room_id:
             messages.error(request, "Please select a room.")
        else: # room_id was provided but wasn't in available_rooms (e.g., race condition)
            messages.error(request, "Invalid room selected or room became full. Please try again.")
        # Fall through to re-render the page with error messages if POST fails

    context = {
        'application': application,
        'available_rooms': available_rooms,
        'target_hostel': target_hostel # Pass target hostel info (can be None)
    }
    return render(request, 'hostel/assign_room.html', context)


# --- Student Hostel Views ---

# View 1: Shows Boys/Girls categories
@login_required
def hostel_category_page(request):
    return render(request, 'hostel/hostel_category.html')

# View 2: Shows UG/PG/PhD categories for the selected gender, with counts
@login_required
def hostel_list(request, gender):
    gender_display = dict(Hostel.GENDER_CHOICES).get(gender)
    levels = Hostel.LEVEL_CHOICES
    level_counts = {}
    # Calculate count for each level within the selected gender
    for level_code, level_name in levels:
        count = Hostel.objects.filter(gender=gender, level=level_code).count()
        level_counts[level_code] = count
    context = {
        'gender_code': gender,
        'gender_display': gender_display,
        'levels': levels,
        'level_counts': level_counts
    }
    return render(request, 'hostel/hostel_list.html', context)

# View 3: Shows the final list of hostels filtered by gender and level
@login_required
def hostel_list_by_level(request, gender, level):
    hostels = Hostel.objects.filter(gender=gender, level=level)
    gender_display = dict(Hostel.GENDER_CHOICES).get(gender)
    level_display = dict(Hostel.LEVEL_CHOICES).get(level)
    context = {
        'hostels': hostels,
        'gender_code': gender,
        'gender_display': gender_display,
        'level_display': level_display,
    }
    return render(request, 'hostel/hostel_list_by_level.html', context)

# View 4: Shows details for a specific hostel, including vacancies
@login_required
def hostel_detail(request, hostel_id):
    hostel = get_object_or_404(Hostel, id=hostel_id)
    # Get rooms, count occupants efficiently
    rooms_with_counts = Room.objects.filter(hostel=hostel).annotate(
        occupant_count=Count('studentapplication')
    )
    total_rooms = rooms_with_counts.count()
    # Determine room capacity (handle case where hostel has no rooms yet)
    room_capacity = 3 # Default assumption
    first_room = rooms_with_counts.first()
    if first_room:
        room_capacity = first_room.capacity
    # Calculate stats
    total_capacity = total_rooms * room_capacity
    total_occupants = rooms_with_counts.aggregate(total=Sum('occupant_count'))['total'] or 0
    available_rooms_count = sum(1 for room in rooms_with_counts if room.occupant_count < room.capacity)
    vacancies = total_capacity - total_occupants
    context = {
        'hostel': hostel,
        'rooms': rooms_with_counts, # Pass individual rooms for detailed breakdown
        'total_rooms': total_rooms,
        'available_rooms_count': available_rooms_count,
        'vacancies': vacancies,
        'room_capacity': room_capacity,
    }
    return render(request, 'hostel/hostel_detail.html', context)

# --- Student Profile View (Handles photo update) ---
@login_required
def student_profile(request):
    application = get_object_or_404(StudentApplication, student=request.user)

    if request.method == 'POST':
        # Pass request.FILES to handle photo uploads
        form = ProfileUpdateForm(request.POST, request.FILES, instance=application)
        if form.is_valid():
            form.save() # Saves changes including photo
            messages.success(request, 'Your profile has been updated successfully!')
            return redirect('profile') # Redirect back to profile page
    else: # GET request
        # Show form pre-filled with current data
        form = ProfileUpdateForm(instance=application)

    context = {
        'form': form,
        'application': application # Pass application object for displaying photo etc.
    }
    return render(request, 'hostel/student_profile.html', context)

# --- PDF Download View ---
@login_required
def download_approval_slip(request):
    application = get_object_or_404(StudentApplication, student=request.user)

    # Ensure the student is approved and has a room
    if application.application_status != 'APPROVED' or not application.assigned_room:
        messages.error(request, "Hostel allotment slip is only available after approval and room assignment.")
        return redirect('dashboard')

    # Get the HTML template
    template_path = 'hostel/approval_slip.html'
    template = get_template(template_path)

    # Define context data for the template
    context = {'application': application} # Pass the application object

    # Render the HTML with context
    html = template.render(context)

    # Create a PDF file in memory
    result = BytesIO()

    # Encode HTML to UTF-8 before passing to pisaDocument
    pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)

    # Check for errors during PDF generation
    if not pdf.err:
        # If successful, create the HTTP response
        response = HttpResponse(result.getvalue(), content_type='application/pdf')
        # Define filename for the download
        filename = f"Hostel_Allotment_Slip_{application.student.username}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    else:
        # If error during PDF generation
        messages.error(request, f"Error generating PDF slip: {pdf.err}")
        return redirect('dashboard')

# --- Re-apply View ---
@login_required
def reapply_application(request):
    application = get_object_or_404(StudentApplication, student=request.user)
    
    # Only allow re-apply if currently rejected
    if application.application_status == 'REJECTED':
        # Reset the application
        application.application_status = 'PENDING'
        application.rejection_message = None # Clear rejection message
        # Clear the document/photo fields that need re-uploading
        application.provisional_letter = None 
        application.fee_receipt = None
        application.photo = None 
        
        # Save only the fields we've changed
        application.save(update_fields=[
            'application_status', 'rejection_message', 
            'provisional_letter', 'fee_receipt', 'photo'
        ])
        
        messages.success(request, "Your application has been reset. Please submit your details and documents again.")
    else:
        messages.error(request, "You can only re-apply if your application was rejected.")
        
    return redirect('dashboard')