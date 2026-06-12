from django.http import HttpResponse


def home(request):
    return HttpResponse('Django development environment is ready.')

# Create your views here.
