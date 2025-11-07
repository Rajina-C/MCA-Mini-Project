from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
from django.db.models import Count, Q, F, Sum
from .models import Hostel, Room, StudentApplication
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

# --- IMPORT THE UNFOLD ADMIN CLASS ---
from unfold.admin import ModelAdmin

User = get_user_model()

# --- Helper Function (No changes) ---
def get_next_actionable_application(user):
    """
    Finds the single oldest PENDING application for the categories
    linked to the current priority hostel(s) managed by the user.
    Returns None if no actionable application exists for this user.
    """
    if user.is_superuser:
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

            if target_hostel and target_hostel in managed_hostels:
                category_oldest_app = StudentApplication.objects.filter(
                    gender=gender, level=level, application_status='PENDING'
                ).order_by('created_at').first()

                if category_oldest_app:
                    if oldest_app_in_eligible_category is None or category_oldest_app.created_at < oldest_app_in_eligible_category.created_at:
                        oldest_app_in_eligible_category = category_oldest_app

        return oldest_app_in_eligible_category 

    return None 

# --- Admin Actions (No changes) ---
@admin.action(description='Approve selected application (FIFO)')
def approve_applications(modeladmin, request, queryset):
    next_app = get_next_actionable_application(request.user)
    approved_count = 0
    for application in queryset:
        if next_app and application.id == next_app.id:
            application.application_status = 'APPROVED'
            application.save()
            approved_count += 1
            next_app = get_next_actionable_application(request.user)
        else:
            messages.warning(request, f"Application for {application.student.username} cannot be approved out of order.")

    if approved_count > 0:
        messages.success(request, f"{approved_count} application(s) approved successfully.")


# --- Student Application Admin (Inherits from ModelAdmin) ---
@admin.register(StudentApplication)
class StudentApplicationAdmin(ModelAdmin): # <--- Uses Unfold
    list_display = ('actionable_link', 'gender', 'level', 'application_status', 'assigned_room', 'submitted_at')
    list_filter = ('application_status', 'gender', 'level', 'assigned_room__hostel__name')
    search_fields = ('student__username',)
    actions = [approve_applications] 
    list_display_links = None 

    def submitted_at(self, obj):
        return obj.created_at
    submitted_at.short_description = 'Submitted at'
    submitted_at.admin_order_field = 'created_at'

    def actionable_link(self, obj):
        next_app = getattr(self, '_next_actionable_app', None) 
        can_click = False
        link_text = obj.student.username
        
        if self.request.user.is_superuser:
            can_click = True
        elif self.request.user.groups.filter(name='Warden').exists():
            if next_app and obj.id == next_app.id:
                can_click = True
                link_text = f"{obj.student.username} (Next)"
        elif self.request.user.groups.filter(name__startswith='Chief Warden').exists():
            can_click = True 
            
        if can_click:
            url = reverse('admin:hostel_studentapplication_change', args=[obj.id])
            return format_html('<a href="{}">{}</a>', url, link_text)
        else:
            status_note = ""
            if obj.application_status != 'PENDING':
                status_note = f" ({obj.get_application_status_display()})"
            elif obj.application_status == 'PENDING':
                is_relevant_category = False
                if hasattr(self, 'request'): 
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
    actionable_link.admin_order_field = 'student__username' 

    def get_queryset(self, request):
        qs = super().get_queryset(request).order_by('created_at')
        self.request = request 

        if request.user.is_superuser:
            return qs.filter(Q(application_status='PENDING') | Q(application_status='APPROVED') | Q(application_status='REJECTED')) 
        elif request.user.groups.filter(name='Chief Warden - Boys').exists():
            return qs.filter(gender='M')
        elif request.user.groups.filter(name='Chief Warden - Girls').exists():
            return qs.filter(gender='F')
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
            
            if not applications_to_show_filter: return qs.none()
            return qs.filter(applications_to_show_filter)

        return qs.none()

    def changelist_view(self, request, extra_context=None):
        self._next_actionable_app = None
        if request.user.groups.filter(name='Warden').exists() or request.user.is_superuser:
            self._next_actionable_app = get_next_actionable_application(request.user)
        self.request = request 
        extra_context = extra_context or {}
        extra_context['next_app_id'] = self._next_actionable_app.id if self._next_actionable_app else None
        return super().changelist_view(request, extra_context=extra_context)

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
                    can_view = True 
                    if (next_app and str(next_app.id) == object_id) or \
                        (app_to_edit.application_status == 'APPROVED' and app_to_edit.assigned_room is None): 
                        can_edit = True
            except StudentApplication.DoesNotExist:
                messages.error(request, "Application not found.")
                return redirect("admin:hostel_studentapplication_changelist")
        elif request.user.groups.filter(name__startswith='Chief Warden').exists():
            try:
                app_to_edit = self.get_object(request, object_id) 
                if app_to_edit:
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
        
        if not can_edit:
            extra_context = extra_context or {}
            extra_context['show_save_and_continue'] = False
            extra_context['show_save'] = False
            extra_context['show_delete'] = not request.user.is_superuser 

        return super().change_view(request, object_id, form_url, extra_context=extra_context)

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = []
        if request.user.groups.filter(name__startswith='Chief Warden').exists():
            readonly_fields = [field.name for field in self.model._meta.fields if field.name != 'id']
        elif request.user.groups.filter(name='Warden').exists():
            readonly_fields = ['student', 'gender', 'level', 'address', 'guardian_contact', 
                            'provisional_letter', 'fee_receipt', 'photo', 'created_at']
        return readonly_fields

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "assigned_room":
            if request.user.groups.filter(name__startswith='Chief Warden').exists():
                kwargs["queryset"] = Room.objects.none() 
                return super().formfield_for_foreignkey(db_field, request, **kwargs)

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

        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            return None 
        return actions


# --- Hostel Admin (Inherits from ModelAdmin) ---
@admin.register(Hostel)
class HostelAdmin(ModelAdmin): # <--- Uses Unfold
    
    # --- 1. Define the Actions (the "buttons") ---
    @admin.action(description='Close selected hostel(s)')
    def close_hostels(self, request, queryset):
        if request.user.groups.filter(name='Warden').exists():
             queryset = queryset.filter(assigned_warden=request.user)
        updated_count = queryset.update(is_manually_closed=True)
        self.message_user(request, f"{updated_count} hostel(s) have been manually closed.", messages.SUCCESS)

    @admin.action(description='Re-open selected hostel(s)')
    def reopen_hostels(self, request, queryset):
        if request.user.groups.filter(name='Warden').exists():
             queryset = queryset.filter(assigned_warden=request.user)
        updated_count = queryset.update(is_manually_closed=False)
        self.message_user(request, f"{updated_count} hostel(s) have been re-opened.", messages.SUCCESS)

    # --- 2. Assign Actions ---
    actions = ['close_hostels', 'reopen_hostels']

    # --- 3. (THIS IS THE FIX) Dynamic List Display ---
    def get_list_display(self, request):
        # Base columns for all roles
        list_display = ('name', 'gender', 'level', 'priority', 'assigned_warden', 'total_rooms', 'available_slots')
        
        # Add the 'is_manually_closed' column ONLY for the Admin
        if request.user.is_superuser:
            list_display += ('is_manually_closed',)
        
        return list_display

    list_display_links = ('name',) # Make name clickable

    # --- 4. Add calculations for new columns ---
    def get_queryset(self, request):
        qs = super().get_queryset(request).annotate(
            total_rooms=Count('room_set', distinct=True),
            total_slots=Sum('room_set__capacity'),
            current_occupants=Count('room_set__studentapplication', distinct=True)
        )
        
        # --- Role-based filtering logic ---
        if request.user.is_superuser:
            return qs  
        if request.user.groups.filter(name='Warden').exists():
            return qs.filter(id__in=request.user.managed_hostels.all())
        if request.user.groups.filter(name='Chief Warden - Boys').exists():
            return qs.filter(gender='M')
        if request.user.groups.filter(name='Chief Warden - Girls').exists():
            return qs.filter(gender='F')
        return qs.none()

    # --- 5. Helper methods for new columns ---
    def total_rooms(self, obj):
        return obj.total_rooms
    total_rooms.short_description = 'Total Rooms'
    total_rooms.admin_order_field = 'total_rooms' 

    def available_slots(self, obj):
        total = obj.total_slots or 0
        occupied = obj.current_occupants or 0
        return total - occupied
    available_slots.short_description = 'Available Slots'

    # --- 6. Control filters ---
    list_filter = ('is_manually_closed', 'gender', 'level', 'assigned_warden')
    search_fields = ('name', 'assigned_warden__username')
    ordering = ('priority',)

    # --- 7. Control who can use the Actions ---
    def get_actions(self, request):
        actions = super().get_actions(request)
        # Chief Wardens CANNOT use actions
        if request.user.groups.filter(name__startswith='Chief Warden').exists():
            return None
        return actions

    # --- 8. Control the "Edit" page ---
    def get_fields(self, request, obj=None):
        fields = ('name', 'gender', 'level', 'priority', 'image', 'description',
                  'warden_name', 'warden_phone', 'warden_email',
                  'assigned_warden')
        # Only Admin sees the checkbox on the edit page
        if request.user.is_superuser:
            return ('name', 'is_manually_closed') + fields
        return fields

    # --- 9. Make page read-only for Wardens/Chiefs ---
    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            # Wardens and Chief Wardens cannot edit hostel details
            # Wardens can ONLY use the "Action" dropdown
            return [field.name for field in self.model._meta.fields if field.name != 'id']
        return []


# --- Room Admin (Inherits from ModelAdmin) ---
@admin.register(Room)
class RoomAdmin(ModelAdmin): # <--- Uses Unfold
    list_display = ('room_number', 'hostel', 'capacity', 'current_occupants', 'vacancy')
    list_filter = ('hostel__gender', 'hostel__level', 'hostel__name') 
    search_fields = ('room_number', 'hostel__name')

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        queryset = queryset.select_related('hostel').annotate(occupant_count=Count('studentapplication'))

        if request.user.groups.filter(name='Chief Warden - Boys').exists():
            return queryset.filter(hostel__gender='M')
        elif request.user.groups.filter(name='Chief Warden - Girls').exists():
            return queryset.filter(hostel__gender='F')
        elif request.user.is_superuser:
            return queryset
        elif request.user.groups.filter(name='Warden').exists():
            managed_hostel_ids = request.user.managed_hostels.values_list('id', flat=True)
            return queryset.filter(hostel__id__in=managed_hostel_ids)

        return queryset.none() 

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


# --- Custom User Admin (No changes, already correct) ---
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group  

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

try:
    admin.site.unregister(User)
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass 

@admin.register(User)
class CustomUserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    
    list_filter = ('is_staff', 'groups')

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )

@admin.register(Group)
class CustomGroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass