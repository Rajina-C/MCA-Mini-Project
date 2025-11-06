# hostel/forms.py
from django import forms
from django.contrib.auth.models import User
from .models import StudentApplication

# --- Simple form for Registration ---
class SimpleRegistrationForm(forms.ModelForm):
    # User fields
    username = forms.CharField(max_length=100, widget=forms.TextInput(
        attrs={'class': 'form-control'}
    ))
    email = forms.EmailField(widget=forms.EmailInput(
        attrs={'class': 'form-control'}
    ))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={'class': 'form-control'}
    ))
    password_confirm = forms.CharField(widget=forms.PasswordInput(
        attrs={'class': 'form-control'}), label="Confirm Password"
    )

    class Meta:
        model = StudentApplication
        fields = ['gender', 'level'] # Only ask for gender and level
        widgets = {
            'gender': forms.Select(attrs={'class': 'form-select'}),
            'level': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean_password_confirm(self):
        password = self.cleaned_data.get('password')
        password_confirm = self.cleaned_data.get('password_confirm')
        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Passwords don't match")
        return password_confirm

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("A user with this username already exists.")
        return username

# --- Form for submitting application details (includes photo) ---
class StudentApplicationForm(forms.ModelForm):
    class Meta:
        model = StudentApplication
        fields = [
            'photo',
            'address',
            'guardian_contact',
            'provisional_letter',
            'fee_receipt',
            'hostel_preference_note'
        ]
        widgets = {
            'photo': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'guardian_contact': forms.TextInput(attrs={'class': 'form-control'}),
            'provisional_letter': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'fee_receipt': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'hostel_preference_note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

# --- UPDATED: Form for the Profile page (includes photo update) ---
class ProfileUpdateForm(forms.ModelForm):
    class Meta:
        model = StudentApplication
        # Added 'photo' to this list
        fields = [
            'photo',
            'address',
            'guardian_contact',
            'hostel_preference_note'
        ]
        # Added the widget for 'photo'
        widgets = {
            'photo': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'guardian_contact': forms.TextInput(attrs={'class': 'form-control'}),
            'hostel_preference_note': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

