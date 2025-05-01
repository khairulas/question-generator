# Educational Question Generator

A Flask web application that generates quiz questions (MCQ, True/False, Short Answer) from course materials using AI (Gemini API). Designed for educators and students.

![App Screenshot](/static/screenshot.png) <!-- Add a screenshot later -->

## Features

- 📝 **AI-Powered Question Generation**  
  Creates questions from uploaded text using Google's Gemini API
- 🎚️ **Customizable Output**  
  Control question types, quantity, and Bloom's Taxonomy level
- 🎨 **Responsive Design**  
  Works on desktop and mobile devices
- 🔒 **Secure**  
  Rate-limited API calls and environment variable protection

## Technologies Used

- **Backend**: Python (Flask)
- **Frontend**: HTML5, CSS3, Jinja2
- **AI Integration**: Gemini API
- **Hosting**: PythonAnywhere
- **Version Control**: Git/GitHub

## Installation

### Prerequisites
- Python 3.10+
- Gemini API key ([Get one here](https://aistudio.google.com/app/apikey))
- PythonAnywhere account (for deployment)

### Local Setup
```bash
# Clone repository
git clone https://github.com/yourusername/question-generator.git
cd question-generator

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Set environment variables
echo "FLASK_SECRET_KEY=your_random_key" > .env
echo "GEMINI_API_KEY=your_api_key" >> .env

# Run application
flask run
