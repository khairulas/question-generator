import os
import sys

# Assuming your Flask app is in app.py
# If your app is in a folder, adjust accordingly
from app import app  

# Add the project directory to sys.path
# This is often necessary on PythonAnywhere
path = os.path.dirname(os.path.abspath(__file__))
if path not in sys.path:
    sys.path.append(path)

# Set environment variables (RECOMMENDED for PythonAnywhere)
os.environ['FLASK_SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')  # Replace!
os.environ['GEMINI_API_KEY'] = os.getenv('GEMINI_API_KEY')    # Replace!

# The 'application' variable is required by PythonAnywhere
application = app