# hostel/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Main auth and dashboard
    path('', views.home, name='home'),
    path('register/', views.register, name='register'),
    path('dashboard/', views.student_dashboard, name='dashboard'),
    path('profile/', views.student_profile, name='profile'),
    
    # Admin-facing view (linked from admin panel)
    path('assign-room/<int:application_id>/', views.assign_room, name='assign_room'),

    # Student-facing Hostel browsing
    path('hostels/', views.hostel_category_page, name='hostel_category'),
    path('hostels/<str:gender>/', views.hostel_list, name='hostel_list'),
    path('hostels/<str:gender>/<str:level>/', views.hostel_list_by_level, name='hostel_list_by_level'),
    path('hostel/<int:hostel_id>/', views.hostel_detail, name='hostel_detail'),

    # Application actions
    path('download-slip/', views.download_approval_slip, name='download_approval_slip'),
    path('re-apply/', views.reapply_application, name='reapply_application'), # <-- The new URL
]