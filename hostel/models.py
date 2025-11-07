# hostel/models.py
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model() # Get the active user model

# 1. Define Hostel FIRST
class Hostel(models.Model):
    GENDER_CHOICES = [
        ('M', 'Boys'),
        ('F', 'Girls'),
    ]
    LEVEL_CHOICES = [
        ('UG', 'Undergraduate'),
        ('PG', 'Postgraduate'),
        ('PHD', 'PhD'),
    ]
    name = models.CharField(max_length=100)
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    level = models.CharField(max_length=3, choices=LEVEL_CHOICES)
    image = models.ImageField(upload_to='hostel_images/', blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    # --- Warden Details ---
    warden_name = models.CharField(max_length=100, blank=True, null=True)
    warden_phone = models.CharField(max_length=15, blank=True, null=True)
    warden_email = models.EmailField(blank=True, null=True)
    # --- End Warden Details ---

    assigned_warden = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        limit_choices_to={'groups__name': 'Warden', 'is_staff': True},
        related_name='managed_hostels'
    )

    # --- Priority Field ---
    priority = models.PositiveIntegerField(
        default=100,
        help_text="Lower numbers mean higher priority (filled first)."
    )
    # --- End Priority Field ---

    # --- Location Field ---
    location_details = models.TextField(
        blank=True,
        null=True,
        help_text="Enter location description (e.g., Near Silver Jubilee Campus, Opposite Central Library)"
    )
    # --- End Location Field ---

    # --- THIS IS THE NEW FIELD ---
    is_manually_closed = models.BooleanField(
        default=False,
        help_text="Check this box to manually close this hostel to new applications, even if it is not full."
    )
    # --- END OF NEW FIELD ---

    def __str__(self):
        return f"{self.name} ({self.get_gender_display()} {self.get_level_display()})"

    # --- UPDATED Helper Method: Check if Hostel is Full ---
    def is_full(self):
        
        # --- THIS IS THE NEW LOGIC ---
        # If the 'manually closed' box is checked, always report as 'full'.
        if self.is_manually_closed:
            return True
        # --- END OF NEW LOGIC ---

        # --- Your existing logic continues below ---
        rooms = self.room_set.all()
        if not rooms.exists():
            return True

        try:
            first_room = rooms.first()
            if first_room is None or not hasattr(first_room, 'capacity'):
                return True
            room_capacity = int(first_room.capacity)
            if room_capacity <= 0:
                return True
        except (ValueError, TypeError) as e:
            return True

        total_rooms_count = rooms.count()
        total_capacity = total_rooms_count * room_capacity

        if total_capacity == 0:
            return True

        # Count students currently assigned to ANY room in this specific hostel
        current_occupants = StudentApplication.objects.filter(
            assigned_room__hostel=self
        ).count()
        
        is_currently_full = current_occupants >= total_capacity
        return is_currently_full
    # --- END OF UPDATED Helper Method ---


# 2. Define Room SECOND (it depends on Hostel)
class Room(models.Model):
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name='room_set')
    room_number = models.CharField(max_length=10)
    capacity = models.PositiveIntegerField(default=3)

    def __str__(self):
        return f"{self.hostel.name} - Room {self.room_number}"

# 3. Define StudentApplication THIRD (it depends on User and Room)
class StudentApplication(models.Model):
    photo = models.ImageField(upload_to='student_photos/', blank=True, null=True)
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
    ]
    LEVEL_CHOICES = [
        ('UG', 'Undergraduate'),
        ('PG', 'Postgraduate'),
        ('PHD', 'PhD'),
    ]

    student = models.OneToOneField(User, on_delete=models.CASCADE, related_name='studentapplication')

    # Fields set at registration
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    level = models.CharField(max_length=3, choices=LEVEL_CHOICES)

    # Optional fields filled later
    address = models.TextField(blank=True, null=True)
    guardian_contact = models.CharField(max_length=15, blank=True, null=True)
    provisional_letter = models.FileField(upload_to='documents/letters/', blank=True, null=True)
    fee_receipt = models.FileField(upload_to='documents/receipts/', blank=True, null=True)
    hostel_preference_note = models.TextField(blank=True, null=True)

    # Admin/Warden fields
    application_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    assigned_room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='studentapplication')

    rejection_message = models.TextField(
        blank=True, null=True, 
        help_text="If rejecting, provide the reason here. This will be visible to the student."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Application for {self.student.username}"