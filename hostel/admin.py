# hostel/admin.py
from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
from django.db.models import Count, Q, F
from .models import Hostel, Room, StudentApplication
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

User = get_user_model()

# --- Helper Function to find the next actionable application for a user ---
def get_next_actionable_application(user):
    """
    Finds the single oldest PENDING application for the categories
    linked to the current priority hostel(s) managed by the user.
    Returns None if no actionable application exists for this user.
    """
    if user.is_superuser:
        # Superuser doesn't strictly follow FIFO here, finds oldest overall pending
        return StudentApplication.objects.filter(application_status='PENDING').order_by('created_at').first()

    if user.groups.filter(name='Warden').exists():
        managed_hostels = user.managed_hostels.all()
        if not managed_hostels:
            return None

        managed_categories = managed_hostels.values_list('gender', 'level').distinct()
        oldest_app_in_eligible_category = None

        for gender, level in managed_categories:
            target_hostel = None
            candidate_hostels = Hostel.objects.filter(
                gender=gender, level=level
            ).order_by('priority')
            for hostel in candidate_hostels:
                if not hostel.is_full():
                    target_hostel = hostel
                    break

            # If a target hostel exists AND this warden manages it
            if target_hostel and target_hostel in managed_hostels:
                # Find the oldest PENDING application for THIS specific category
                category_oldest_app = StudentApplication.objects.filter(
                    gender=gender, level=level, application_status='PENDING'
                ).order_by('created_at').first()

                # Keep track of the absolute oldest among the eligible categories
                if category_oldest_app:
                    if oldest_app_in_eligible_category is None or category_oldest_app.created_at < oldest_app_in_eligible_category.created_at:
                        oldest_app_in_eligible_category = category_oldest_app

        return oldest_app_in_eligible_category # This is the single "next" app

    return None # Not superuser, not warden

# --- Admin Actions (Restricted by FIFO) ---
@admin.action(description='Approve selected application (FIFO)')
def approve_applications(modeladmin, request, queryset):
    next_app = get_next_actionable_application(request.user)
    approved_count = 0
    for application in queryset:
        if next_app and application.id == next_app.id:
            application.application_status = 'APPROVED'
            application.save()
            approved_count += 1
            # Recalculate next app after approval might change priority hostel
            next_app = get_next_actionable_application(request.user)
        else:
            messages.warning(request, f"Application for {application.student.username} cannot be approved out of order.")

    if approved_count > 0:
         messages.success(request, f"{approved_count} application(s) approved successfully.")


# --- Student Application Admin ---
@admin.register(StudentApplication)
class StudentApplicationAdmin(admin.ModelAdmin):
    # Add 'actionable_link' instead of direct link on student
    list_display = ('actionable_link', 'gender', 'level', 'application_status', 'assigned_room', 'submitted_at')
    list_filter = ('application_status', 'gender', 'level', 'assigned_room__hostel__name')
    search_fields = ('student__username',)
    actions = [approve_applications] # Keep the action, but restrict selection
    list_display_links = None # Disable default link on first column

    def submitted_at(self, obj):
        return obj.created_at
    submitted_at.short_description = 'Submitted at'
    submitted_at.admin_order_field = 'created_at'

    # --- Custom column to show link ONLY for the next actionable app ---
    def actionable_link(self, obj):
        next_app = getattr(self, '_next_actionable_app', None) # Get cached next app
        can_click = False
        link_text = obj.student.username
        
        # Determine who can click and what the link text should be
        if self.request.user.is_superuser:
            can_click = True
        elif self.request.user.groups.filter(name='Warden').exists():
            if next_app and obj.id == next_app.id:
                can_click = True
                link_text = f"{obj.student.username} (Next)"
        # Chief Wardens get a view-only link (if they have change permission) or just text
        elif self.request.user.groups.filter(name__startswith='Chief Warden').exists():
             can_click = True # Allow clicking to view read-only page
             
        # Format the display
        if can_click:
            url = reverse('admin:hostel_studentapplication_change', args=[obj.id])
            return format_html('<a href="{}">{}</a>', url, link_text)
        else:
            # Display non-pending statuses differently if needed
            status_note = ""
            if obj.application_status != 'PENDING':
                 status_note = f" ({obj.get_application_status_display()})"
            elif obj.application_status == 'PENDING':
                 # Check if it belongs to a category handled by this warden at all
                 is_relevant_category = False
                 if hasattr(self, 'request'): # Ensure request is available
                     if self.request.user.groups.filter(name='Warden').exists():
                         for h in self.request.user.managed_hostels.all():
                             if h.gender == obj.gender and h.level == obj.level:
                                 is_relevant_category = True
                                 break
                     elif self.request.user.is_superuser:
                         is_relevant_category = True
                 if is_relevant_category:
                      status_note = " (Waiting in queue)"
            return f"{obj.student.username}{status_note}"
    actionable_link.short_description = 'Student'
    actionable_link.admin_order_field = 'student__username' # Allow sorting by username

    # --- UPDATED: get_queryset for ALL roles ---
    def get_queryset(self, request):
        qs = super().get_queryset(request).order_by('created_at')
        self.request = request # Store request for other methods

        # Superuser sees all relevant statuses
        if request.user.is_superuser:
            return qs.filter(Q(application_status='PENDING') | Q(application_status='APPROVED') | Q(application_status='REJECTED')) # Show all

        # Chief Warden - Boys sees all Boy applications (all statuses)
        elif request.user.groups.filter(name='Chief Warden - Boys').exists():
            return qs.filter(gender='M')

        # Chief Warden - Girls sees all Girl applications (all statuses)
        elif request.user.groups.filter(name='Chief Warden - Girls').exists():
            return qs.filter(gender='F')

        # Regular Wardens see only PENDING for their current priority hostel
        elif request.user.groups.filter(name='Warden').exists():
            managed_hostels = request.user.managed_hostels.all()
            if not managed_hostels: return qs.none()
            managed_categories = managed_hostels.values_list('gender', 'level').distinct()
            applications_to_show_filter = Q()
            for gender, level in managed_categories:
                target_hostel = None
                candidate_hostels = Hostel.objects.filter(gender=gender, level=level).order_by('priority')
                for hostel in candidate_hostels:
                    if not hostel.is_full():
                        target_hostel = hostel; break
                if target_hostel and target_hostel in managed_hostels:
                    applications_to_show_filter |= Q(gender=gender, level=level, application_status='PENDING')
            
            # Also include apps this warden has ALREADY APPROVED but not yet assigned
            # Or just all their approved apps? Let's add all approved/rejected they manage
            # applications_to_show_filter |= Q(assigned_room__hostel__in=managed_hostels)
            # applications_to_show_filter |= Q(application_status__in=['APPROVED', 'REJECTED'], gender__in=[c[0] for c in managed_categories], level__in=[c[1] for c in managed_categories])
            
            if not applications_to_show_filter: return qs.none()
            return qs.filter(applications_to_show_filter)

        # Other staff see nothing
        return qs.none()
    # --- END OF UPDATED get_queryset ---

    def changelist_view(self, request, extra_context=None):
        # Calculate next app only needed if user is Warden or superuser
        self._next_actionable_app = None
        if request.user.groups.filter(name='Warden').exists() or request.user.is_superuser:
             self._next_actionable_app = get_next_actionable_application(request.user)
        self.request = request # Store request for actionable_link
        extra_context = extra_context or {}
        extra_context['next_app_id'] = self._next_actionable_app.id if self._next_actionable_app else None
        return super().changelist_view(request, extra_context=extra_context)

    # --- UPDATED: change_view for Chief Wardens (Read-Only) ---
    def change_view(self, request, object_id, form_url='', extra_context=None):
        can_edit = False
        can_view = False

        if request.user.is_superuser:
            can_edit = True
            can_view = True
        elif request.user.groups.filter(name='Warden').exists():
            next_app = get_next_actionable_application(request.user)
            try:
                app_to_edit = self.get_object(request, object_id)
                if app_to_edit:
                    can_view = True # Allow viewing if it passed get_queryset
                    if (next_app and str(next_app.id) == object_id) or \
                       (app_to_edit.application_status == 'APPROVED' and app_to_edit.assigned_room is None): # Allow assigning room if approved but unassigned
                        can_edit = True
            except StudentApplication.DoesNotExist:
                messages.error(request, "Application not found.")
                return redirect("admin:hostel_studentapplication_changelist")
        elif request.user.groups.filter(name__startswith='Chief Warden').exists():
            try:
                app_to_edit = self.get_object(request, object_id) # Check if app exists
                if app_to_edit:
                    # Check if chief warden is allowed to view this (gender match)
                    if (request.user.groups.filter(name='Chief Warden - Boys').exists() and app_to_edit.gender == 'M') or \
                       (request.user.groups.filter(name='Chief Warden - Girls').exists() and app_to_edit.gender == 'F'):
                        can_view = True
            except StudentApplication.DoesNotExist:
                 messages.error(request, "Application not found.")
                 return redirect("admin:hostel_studentapplication_changelist")
            
            extra_context = extra_context or {}
            extra_context['show_save_and_continue'] = False
            extra_context['show_save'] = False
            extra_context['show_delete'] = False

        if not can_view:
             messages.error(request, "You do not have permission to view this application.")
             return redirect("admin:hostel_studentapplication_changelist")
        
        # If view allowed but edit not (e.g., Warden viewing non-next app, or Chief Warden)
        if not can_edit:
             extra_context = extra_context or {}
             extra_context['show_save_and_continue'] = False
             extra_context['show_save'] = False
             extra_context['show_delete'] = not request.user.is_superuser # Only superuser can delete?

        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    # Make fields read-only for Chief Wardens dynamically
    def get_readonly_fields(self, request, obj=None):
         readonly_fields = []
         if request.user.groups.filter(name__startswith='Chief Warden').exists():
              readonly_fields = [field.name for field in self.model._meta.fields if field.name != 'id']
         elif request.user.groups.filter(name='Warden').exists():
             # Wardens can only change status, room, and rejection message
             # Make other fields read-only
             readonly_fields = ['student', 'gender', 'level', 'address', 'guardian_contact', 
                                'provisional_letter', 'fee_receipt', 'photo', 'created_at']
             # If obj is already approved, maybe make status read-only too?
             # if obj and obj.application_status == 'APPROVED':
             #    readonly_fields.append('application_status')
         return readonly_fields

    # --- formfield_for_foreignkey (Minor update for Chief Warden view) ---
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "assigned_room":
            if request.user.groups.filter(name__startswith='Chief Warden').exists():
                 kwargs["queryset"] = Room.objects.none() # Chiefs don't assign rooms
                 return super().formfield_for_foreignkey(db_field, request, **kwargs)

            # --- Keep existing priority/warden logic for assignment ---
            obj_id = request.resolver_match.kwargs.get('object_id')
            application = None
            if obj_id:
                try: application = StudentApplication.objects.get(pk=obj_id)
                except StudentApplication.DoesNotExist: pass

            target_hostel = None
            if application:
                candidate_hostels = Hostel.objects.filter(
                    gender=application.gender, level=application.level
                ).order_by('priority')
                for hostel in candidate_hostels:
                    if not hostel.is_full():
                        target_hostel = hostel; break

            room_queryset = Room.objects.none()
            if target_hostel:
                can_manage_target = False
                if request.user.is_superuser: can_manage_target = True
                elif request.user.groups.filter(name='Warden').exists():
                    if target_hostel in request.user.managed_hostels.all():
                        can_manage_target = True
                if can_manage_target:
                    room_queryset = Room.objects.filter(hostel=target_hostel).annotate(
                        num_students=Count('studentapplication')
                    ).filter(num_students__lt=F('capacity'))
            kwargs["queryset"] = room_queryset
            # --- End existing logic ---

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    # --- get_actions (UPDATED for Chief Wardens) ---
    def get_actions(self, request):
        actions = super().get_actions(request)
        # Disable actions for Wardens and Chief Wardens
        if not request.user.is_superuser:
            return None # Disables bulk actions dropdown entirely
        return actions


# --- Hostel Admin (No changes needed) ---
@admin.register(Hostel)
class HostelAdmin(admin.ModelAdmin):
    list_display = ('name', 'gender', 'level', 'priority', 'assigned_warden')
    list_filter = ('gender', 'level', 'assigned_warden')
    search_fields = ('name', 'assigned_warden__username')
    fields = ('name', 'gender', 'level', 'priority', 'image', 'description',
              'warden_name', 'warden_phone', 'warden_email',
              'assigned_warden')
    ordering = ('priority',)

# --- Room Admin (UPDATED for Chief Wardens) ---
@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
   list_display = ('room_number', 'hostel', 'capacity', 'current_occupants', 'vacancy')
   list_filter = ('hostel__gender', 'hostel__level', 'hostel__name') # Add gender/level filters
   search_fields = ('room_number', 'hostel__name')

   # --- UPDATED: get_queryset for Chief Wardens ---
   def get_queryset(self, request):
       queryset = super().get_queryset(request)
       queryset = queryset.select_related('hostel').annotate(occupant_count=Count('studentapplication'))

       # Filter rooms based on Chief Warden role
       if request.user.groups.filter(name='Chief Warden - Boys').exists():
            return queryset.filter(hostel__gender='M')
       elif request.user.groups.filter(name='Chief Warden - Girls').exists():
            return queryset.filter(hostel__gender='F')
       # Superuser sees all
       elif request.user.is_superuser:
            return queryset
       # Regular wardens only see rooms in their managed hostels
       elif request.user.groups.filter(name='Warden').exists():
           managed_hostel_ids = request.user.managed_hostels.values_list('id', flat=True)
           return queryset.filter(hostel__id__in=managed_hostel_ids)

       return queryset.none() # Other staff see no rooms
   # --- END OF UPDATED get_queryset ---

   def current_occupants(self, obj):
       return obj.occupant_count
   current_occupants.short_description = 'Occupants'
   current_occupants.admin_order_field = 'occupant_count'

   def vacancy(self, obj):
       try:
           capacity = int(obj.capacity)
           occupants = int(obj.occupant_count)
           return capacity - occupants
       except (ValueError, TypeError):
           return "N/A"
   vacancy.short_description = 'Vacancy'