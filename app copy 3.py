import io
import re
import sys
import uuid
import json
from flask import Flask, current_app, flash, jsonify, render_template, request, redirect, send_file, session, url_for
from flask_migrate import Migrate
from flask import Flask, render_template
from flask_wtf.csrf import CSRFProtect, generate_csrf
import qrcode
import requests
import google.generativeai as genai
from bleach import clean
import logging
import os
import time
from datetime import datetime
from collections import defaultdict
from sqlalchemy.exc import OperationalError
from sqlalchemy import text 
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from markupsafe import Markup
import markdown
import pdfplumber
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature


from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# --- CORRECTED CONFIGURATION ORDER ---
# 1. Load the secret key from the environment first.
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')



# 2. Configure other app settings.
#DATABASE_URL = os.getenv('DATABASE_URL')
#if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
#    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
# Check for a MySQL connection string from the environment variables
if os.getenv('MYSQL_USER') and os.getenv('MYSQL_PASSWORD') and os.getenv('MYSQL_HOST') and os.getenv('MYSQL_DB'):
    # This block will run on PythonAnywhere
    username = os.getenv('MYSQL_USER')
    password = os.getenv('MYSQL_PASSWORD')
    hostname = os.getenv('MYSQL_HOST')
    database = os.getenv('MYSQL_DB')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{username}:{password}@{hostname}/{database}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.dirname(__file__), 'instance/questions.db')
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 3. Initialize extensions AFTER the main config is set.
csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db) 
mail = Mail(app)

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() in ['true', '1', 't']
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')
# --  ---

@app.errorhandler(500)
def handle_internal_server_error(e):
    app.logger.error(f"Internal Server Error (500): {e}", exc_info=True)
    db.session.rollback()
    message = "We're sorry, something went wrong on our end. We've been notified and are looking into it."
    return render_template('error.html', message=message), 500

@app.errorhandler(OperationalError) # Use app_errorhandler to catch errors globally in a Blueprint
def handle_db_connection_error(e):
    """
    Catches and handles database connection errors for the entire application.
    This is typically for errors like the database server being down.
    """
    # Log the detailed error for debugging purposes
    current_app.logger.error(f"Database Connection Error: {e}", exc_info=True)

    # It's crucial to rollback the session to a clean state
    db.session.rollback()

    # Return a user-friendly error page with a 503 "Service Unavailable" status
    message = "We're sorry, but we're currently experiencing technical difficulties. Please try again later."
    return render_template('error.html', message=message), 503



# App models

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirect to login page if user is not authenticated

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    quizzes = db.relationship('Quiz', backref='creator', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_reset_token(self, expires_sec=3600):
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        return s.dumps(self.id, salt='password-reset-salt')

    @staticmethod
    def verify_reset_token(token):
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        try:
            user_id = s.loads(token, salt='password-reset-salt', max_age=3600)
        except (SignatureExpired, BadTimeSignature):
            return None
        return User.query.get(user_id)
    
# Question model
class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(20))
    bloom_level = db.Column(db.String(20))
    options = db.Column(db.Text)
    answer = db.Column(db.Text)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'))
    marks = db.Column(db.Integer, nullable=False, default=5)

# This is the new Quiz model with a public_id column
class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(100))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    analysis_text = db.Column(db.Text, nullable=True) # Ensure this line is present
    questions = db.relationship('Question', backref='quiz', cascade="all, delete-orphan")

    @property
    def total_score(self):
        """Calculates the total score for the quiz on the fly."""
        return sum(question.marks for question in self.questions if question.marks is not None)

class QuizAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_name = db.Column(db.String(100), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    total_score = db.Column(db.Integer, nullable=False)
    percentage = db.Column(db.Float, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    quiz = db.relationship('Quiz', backref=db.backref('attempts', lazy=True))
    answers = db.relationship('StudentAnswer', backref='attempt', lazy=True, cascade="all, delete-orphan")

class StudentAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey('quiz_attempt.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    answer_text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    question = db.relationship('Question')


app.secret_key = os.getenv('FLASK_SECRET_KEY')

logging.basicConfig(filename='app.log', level=logging.ERROR)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
genai.configure(api_key=GEMINI_API_KEY)

USER_TOKENS = defaultdict(lambda: {'tokens': 5, 'last_refill': time.time()})
TOKEN_CAPACITY = 5
REFILL_RATE = 1  # 1 token per second

def is_rate_limited(user_ip):
    now = time.time()
    user_data = USER_TOKENS[user_ip]

    # Refill tokens
    time_elapsed = now - user_data['last_refill']
    user_data['tokens'] = min(TOKEN_CAPACITY, user_data['tokens'] + time_elapsed * REFILL_RATE)
    user_data['last_refill'] = now

    if user_data['tokens'] >= 1:
        user_data['tokens'] -= 1
        return False  # Not rate limited
    else:
        return True   # Rate limited
    
    
@app.template_filter('markdown')
def markdown_filter(s):
    return Markup(markdown.markdown(s))

def validate_input(form):
    errors = {}
    course_material = form.get('course_material', '')
    question_types = form.getlist('question_types')
    num_questions = form.get('num_questions', '')
    bloom_level = form.get('bloom_level', '')

    if not course_material:
        errors['course_material'] = "Course material is required."
    if not question_types:
        errors['question_types'] = "At least one question type is required."
    try:
        num_questions = int(num_questions)
        if not 1 <= num_questions <= 20:
            errors['num_questions'] = "Number of questions must be between 1 and 20."
    except ValueError:
        errors['num_questions'] = "Invalid number of questions."
    allowed_question_types = ["MCQ", "True/False", "Short Answer"]
    for q_type in question_types:
        if q_type not in allowed_question_types:
            errors['question_types'] = f"Invalid question type: {q_type}"
    allowed_bloom_levels = ["Remembering", "Understanding", "Applying", "Analyzing", "Evaluating", "Creating"]
    if bloom_level not in allowed_bloom_levels:
        errors['bloom_level'] = "Invalid Bloom's level."

    return errors, course_material, question_types, num_questions, bloom_level

# Main route to create quiz
# This route handles both GET and POST requests for creating a quiz
def extract_text_from_pdf(pdf_stream):
    """Extracts text from a PDF file stream."""
    text = ""
    try:
        with pdfplumber.open(pdf_stream) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        return text
    except Exception as e:
        logging.error(f"Error extracting text from PDF: {e}")
        return None

# Main route to create quiz
# This route handles both GET and POST requests for creating a quiz
@app.route('/create-quiz', methods=['GET', 'POST'])
@login_required
def create_quiz():
    if request.method == 'POST':
        # --- Start of logic for extract material from text input or a PDF document ---
        
        # 1. Initialize course_material variable
        course_material = ""
        form_data = request.form
        
        # 2. Check for an uploaded PDF file first
        if 'pdf_file' in request.files:
            pdf_file = request.files['pdf_file']
            if pdf_file.filename != '':
                if not pdf_file.filename.lower().endswith('.pdf'):
                    flash("Invalid file type. Please upload a PDF.", 'danger')
                    return redirect(url_for('index'))
                
                # Call the extraction function ONLY when a valid file exists
                course_material = extract_text_from_pdf(pdf_file.stream)
                
                if course_material is None:
                    flash("Could not extract text from the PDF. The file might be corrupted or image-based.", 'danger')
                    return redirect(url_for('index'))
        
        # 3. If no text was extracted from a PDF, fall back to the textarea
        if not course_material:
            course_material = form_data.get('course_material')

        # 4. Now, validate that we have material from one of the sources
        if not course_material.strip():
            flash("No course material provided. Please paste text or upload a PDF.", 'danger')
            return redirect(url_for('index'))

        # 5. Validate the rest of the form inputs
        if not form_data.getlist('question_types'):
            flash("Please select at least one question type.", 'danger')
            return redirect(url_for('index'))

        # --- End of logic for extract material from text input or a PDF document ---

        # --- Start of validation and question generation logic ---
        sanitized_course_material = clean(course_material)
        question_types = form_data.getlist('question_types')
        num_questions = int(form_data.get('num_questions', 2))
        bloom_level = form_data.get('bloom_level')
        
        questions_text = generate_questions(sanitized_course_material, question_types, num_questions, bloom_level)
        
        questions_list_for_display = parse_questions(questions_text)

        # --- START:  sorting logic ---

        # 1. Define the desired order of question types. You can change this list to alter the order.
        question_type_order = ["True/False", "MCQ", "Fill-in-the-Blank", "Short Answer"]

        # 2. Sort the list of questions based on your custom order.
        # This works by finding the index of each question's type in your order list.
        sorted_questions = sorted(
            questions_list_for_display,
            key=lambda q: question_type_order.index(q.get('type', '')) if q.get('type') in question_type_order else len(question_type_order)
        )
        
        # --- END: sorting logic ---

        session['generated_questions'] = questions_text
        
        csrf_token = generate_csrf()
        
        return render_template(
            'results.html', 
            questions_list=sorted_questions, # Use the new, sorted list here
            raw_questions_text=questions_text,
            csrf_token=csrf_token
        )

    # For GET requests
    csrf_token = generate_csrf()
    return render_template('index.html', csrf_token=csrf_token)


# Function to generate questions using Gemini API
# Updated to request JSON output

def generate_questions(material, types, count, bloom_level):
    model = genai.GenerativeModel('gemini-1.5-flash')
    type_string = ", ".join(types)

    # NEW: Updated prompt requesting JSON output
    prompt = f"""
    Generate exactly {count} quiz questions based on the provided course material.
    The questions should be of the following types: {type_string}.
    Adhere to the Bloom's Taxonomy level of '{bloom_level}'.

    CRITICAL: You MUST respond with only a valid JSON array of objects. Do not include any introductory text, explanations, or markdown formatting outside of the JSON block.

    The JSON array should contain one object for each question. Each object must have the following keys:
    - "type": (String) The type of question ("True/False", "MCQ", "Fill-in-the-Blank", or "Short Answer").
    - "marks": (Integer) A suggested mark, from 1 to 5, based on complexity.
    - "bloom_level": (String) The Bloom's level you targeted.
    - "text": (String) The content of the question itself.
    - "options": (Array of Strings) For "MCQ" questions, an array of four option strings. For other types, this should be an empty array [].
    - "answer": (String) The correct answer. For MCQs, this should be the full text of the correct option.

    JSON Structure Example:
    [
      {{
        "type": "True/False",
        "marks": 1,
        "bloom_level": "Understanding",
        "text": "Structured data is the most common type of data generated today.",
        "options": [],
        "answer": "False"
      }},
      {{
        "type": "MCQ",
        "marks": 3,
        "bloom_level": "Remembering",
        "text": "What is the primary characteristic of big data?",
        "options": ["Small volume", "High velocity", "Simple structure", "Limited sources"],
        "answer": "High velocity"
      }},

      {{
        "type": "Fill-in-the-Blank",
        "marks": 2,
        "bloom_level": "Remembering",
        "text": "The command to initialize a new Git repository is 'git ____'.",
        "options": [],
        "answer": "init"
      }}
    ]

    COURSE MATERIAL:
    "{material}"
    """
    try:
        response = model.generate_content(prompt)
        # We now expect the response.text to be a JSON string
        return response.text
    except Exception as e:
        logging.error(f"Gemini API Error: {str(e)}")
        return "An error occurred while generating questions. Please try again later.", 500
    
# Route to save questions. 
# it is called when the user clicks the "Save Questions" button in the results.html template.

@app.route('/save-questions', methods=['POST'])
@login_required
def save_questions():
    questions_text = request.form.get('questions_text')
    if not questions_text:
        return "No questions to save", 400

    quiz_title = request.form.get('quiz_title', 'Unnamed Quiz')
    parsed_questions = parse_questions(questions_text)

    if not parsed_questions:
        return "Error: Could not parse any questions", 400

    try:
        new_quiz = Quiz(title=quiz_title, user_id=current_user.id)

        for q_data in parsed_questions:
            new_q = Question(
                content=q_data.get('text'),
                question_type=q_data.get('type'),
                bloom_level=q_data.get('bloom_level'),  # <-- CRUCIAL FIX: Use the parsed value
                answer=q_data.get('answer'),
                marks=q_data.get('marks'),
                options=q_data.get('options', '')
            )
            new_quiz.questions.append(new_q)

        db.session.add(new_quiz)
        db.session.commit()

        return redirect(url_for('view_quiz', public_id=new_quiz.public_id))

    except Exception as e:
        db.session.rollback()
        logging.error(f"Database error while saving quiz: {str(e)}")
        return render_template('error.html', message=f"A database error occurred: {str(e)}")


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if not request.form.get('agree_terms'):
            flash('You must agree to the Terms of Use and Privacy Policy to create an account.', 'danger')
            return redirect(url_for('register'))
        
        # Check if username or email already exists
        user_by_username = User.query.filter_by(username=username).first()
        if user_by_username:
            flash('Username already exists. Please choose a different one.', 'danger')
            return redirect(url_for('register'))
        
        user_by_email = User.query.filter_by(email=email).first()
        if user_by_email:
            flash('Email address is already registered.', 'danger')
            return redirect(url_for('register'))
            
        # Check if passwords match
        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
            return redirect(url_for('register'))
            
        # Add password complexity rules if desired (e.g., minimum length)
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
            return redirect(url_for('register'))

        # If all checks pass, create the new user
        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Account created successfully! You are now logged in.', 'success')
        login_user(new_user)
        return redirect(url_for('index')) # Redirect to the dashboard
    csrf_token = generate_csrf()
    return render_template('register.html', csrf_token=csrf_token)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user is None or not user.check_password(password):
            return 'Invalid username or password' # Or render template with error
        login_user(user)
        csrf_token = generate_csrf()
        return redirect(url_for('index'))
    csrf_token = generate_csrf()
    return render_template('login.html', csrf_token=csrf_token)

def send_reset_email(user):
    token = user.get_reset_token()
    msg = Message('Password Reset Request',
                  recipients=[user.email])
    msg.html = render_template('reset_password_email.html', user=user, token=token)
    mail.send(msg)


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            send_reset_email(user)
        # Always show the same message to prevent user enumeration
        flash('If an account with that email exists, a password reset link has been sent.', 'success')
        return redirect(url_for('login'))
    csrf_token = generate_csrf()
    return render_template('forgot_password.html', csrf_token=csrf_token)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = User.verify_reset_token(token)
    if not user:
        flash('That is an invalid or expired token.', 'danger')
        return redirect(url_for('forgot_password'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('reset_password', token=token))

        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
            return redirect(url_for('reset_password', token=token))

        user.set_password(password)
        db.session.commit()
        flash('Your password has been updated! You are now able to log in.', 'success')
        return redirect(url_for('login'))

    csrf_token = generate_csrf()
    return render_template('reset_password.html', token=token, csrf_token=csrf_token)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Route to change password
# This route will allow the user to change their password after logging in

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        # Verify the old password is correct
        if not current_user.check_password(old_password):
            flash('Your old password was incorrect. Please try again.', 'danger')
            return redirect(url_for('change_password'))

        # Check if the new passwords match
        if new_password != confirm_password:
            flash('The new passwords do not match.', 'danger')
            return redirect(url_for('change_password'))
            
        # Check for password complexity (e.g., minimum length)
        if len(new_password) < 8:
            flash('Your new password must be at least 8 characters long.', 'danger')
            return redirect(url_for('change_password'))

        # If all checks pass, update the password
        current_user.set_password(new_password)
        db.session.commit()

        flash('Your password has been updated successfully!', 'success')
        return redirect(url_for('index')) # Redirect to the dashboard

    csrf_token = generate_csrf()
    return render_template('change_password.html', csrf_token=csrf_token)


# This function parses the questions text generated by Gemini.
# It extracts the question type, marks, Bloom's level, question text, options, and answer.

def parse_questions(questions_text):
    """
    Parses a JSON string from Gemini into a list of question dictionaries.
    This is more robust than parsing plain text.
    """
    try:
        # The AI might wrap the JSON in markdown fences (```json ... ```), so we clean it.
        # This regex finds the content between the first '{' or '[' and the last '}' or ']'.
        json_match = re.search(r'\[.*\]|\{.*\}', questions_text, re.DOTALL)
        if not json_match:
            logging.error(f"Could not find valid JSON in AI response: {questions_text}")
            return []

        clean_json_str = json_match.group(0)
        
        # Parse the cleaned string into a Python list of dictionaries
        parsed_data = json.loads(clean_json_str)

        # --- Data Transformation Step ---
        # The new parser returns a list of dicts. The old code expected a specific format.
        # We'll transform the JSON into the format the rest of your app expects.
        questions_for_app = []
        for q in parsed_data:
            new_q = {
                'type': q.get('type'),
                'marks': q.get('marks'),
                'bloom_level': q.get('bloom_level'),
                'text': q.get('text'),
                'answer': q.get('answer'),
                # For MCQs, join the options array into a single string with newlines
                'options': '\n'.join(q.get('options', []))
            }
            questions_for_app.append(new_q)

        return questions_for_app

    except json.JSONDecodeError as e:
        logging.error(f"JSON Parsing Error: {e}\nRaw Response was:\n{questions_text}")
        return [] # Return an empty list to prevent the app from crashing
    except Exception as e:
        logging.error(f"An unexpected error occurred in parse_questions: {e}")
        return []

# Route to view a quiz
# This route will display the quiz details and questions to the user
@app.route('/quiz/<public_id>')
@login_required
def view_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    
    # Define the desired sort order
    question_type_order = ["MCQ", "True/False", "Fill-in-the-Blank", "Short Answer"]
    
    # Sort the questions fetched from the database
    sorted_questions = sorted(
        quiz.questions,
        key=lambda q: question_type_order.index(q.question_type) if q.question_type in question_type_order else len(question_type_order)
    )
    
    # Pass the newly sorted list to the template
    return render_template('quiz.html', quiz=quiz, questions=sorted_questions)

# Route to take the quiz
#  This route will render the quiz form for the user to fill out
@app.route('/quiz/<public_id>/take', methods=['GET'])
def take_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    
    # Define the desired sort order
    question_type_order = ["MCQ", "True/False", "Fill-in-the-Blank", "Short Answer"]
    
    # Sort the questions fetched from the database
    sorted_questions = sorted(
        quiz.questions,
        key=lambda q: question_type_order.index(q.question_type) if q.question_type in question_type_order else len(question_type_order)
    )
    
    # Pass both the quiz and the sorted questions list to the template
    return render_template('take_quiz.html', quiz=quiz, questions=sorted_questions)

@app.route('/quiz/<public_id>/qr')
def quiz_qr_code(public_id):
    """Generates a QR code for the quiz link."""
    # Construct the full URL for the student to take the quiz
    quiz_url = url_for('take_quiz', public_id=public_id, _external=True)
    
    # Generate the QR code image in memory
    img = qrcode.make(quiz_url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    
    # Return the image directly
    return send_file(buf, mimetype='image/png')

# Route to submit the quiz
# This route will handle the form submission from the quiz page
# In app.py

@app.route('/quiz/<public_id>/submit', methods=['POST'])
def submit_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    questions = quiz.questions
    
    score = 0
    total_score = quiz.total_score
    results_for_template = []

    # --- START: Add this new sorting logic ---
    question_type_order = ["True/False", "MCQ", "Fill-in-the-Blank", "Short Answer"]
    sorted_questions = sorted(
        questions,
        key=lambda q: question_type_order.index(q.question_type) if q.question_type in question_type_order else len(question_type_order)
    )
    # --- END: New sorting logic ---

    try:
        student_name = request.form.get('student_name', 'Anonymous')
        # Create the attempt record but don't add final scores yet
        new_attempt = QuizAttempt(
            quiz_id=quiz.id, # Use the integer id from the quiz object
            student_name=student_name,
            score=0, 
            total_score=total_score,
            percentage=0
        )

        for question in sorted_questions:
            student_answer_text = request.form.get(f'question_{question.id}', 'Not Answered')
            correct_answer_text = question.answer.strip()
            
            is_correct = False
            if question.question_type in ['MCQ', 'True/False', 'Fill-in-the-Blank']:
                is_correct = student_answer_text.strip().lower() == correct_answer_text.lower()
            elif question.question_type == 'Short Answer':
                if student_answer_text != 'Not Answered':
                    is_correct = grade_short_answer_with_gemini(correct_answer_text, student_answer_text)

            if is_correct:
                score += question.marks
            
            # Create the student answer record
            student_answer_record = StudentAnswer(
                answer_text=student_answer_text,
                is_correct=is_correct,
                question=question
            )
            # Append the answer record to the attempt's answers relationship
            new_attempt.answers.append(student_answer_record)

            results_for_template.append({
                'question': question,
                'student_answer': student_answer_text,
                'correct_answer': correct_answer_text,
                'is_correct': is_correct
            })

        # Now, calculate the final percentage
        if total_score > 0:
            percentage = round((score / total_score) * 100, 2)
        else:
            percentage = 0
            
        # Update the attempt record with the final calculated score and percentage
        new_attempt.score = score
        new_attempt.percentage = percentage
        
        # Add the completed attempt to the session and commit
        db.session.add(new_attempt)
        db.session.commit()

        return render_template('quiz_results.html', 
                               score=score, 
                               total_score=total_score,
                               percentage=percentage,
                               quiz=quiz,
                               results=results_for_template)
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error submitting quiz {public_id}: {str(e)}")
        return render_template('error.html', message=f"An error occurred while submitting your quiz. Error: {str(e)}")

def create_grader_prompt(correct_answer, student_answer):
    return f"""
    You are an expert examiner grading a short-answer question in a quiz.
    Your task is to determine if the student's answer is correct based on the provided answer key.
    The student does not need to use the exact same words, but their answer must be semantically and factually correct.
    It can be a subset of the provided answer, as long as it is accurate.

    **Answer Key:** "{correct_answer}"
    **Student's Answer:** "{student_answer}"

    Analyze the student's answer and determine its correctness.
    Respond in JSON format with two keys:
    1. "is_correct": a boolean value (true if the answer is correct, false otherwise).
    2. "justification": a brief, one-sentence explanation for your decision.

    Example Response:
    {{
      "is_correct": true,
      "justification": "The student correctly identified the main concept."
    }}
    """


def grade_short_answer_with_gemini(correct_answer, student_answer):
    """Sends answers to Gemini for grading and parses the JSON response."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = create_grader_prompt(correct_answer, student_answer)
        
        response = model.generate_content(prompt)
        
        # Clean the response to ensure it's valid JSON
        cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
        
        # Parse the JSON response from the model
        grade_data = json.loads(cleaned_response)
        
        return grade_data.get('is_correct', False)

    except Exception as e:
        logging.error(f"Gemini API grading error: {str(e)}")
        # Default to False if the API call fails or response is malformed
        return False
    
#  Route to generate overall analysis of a quiz
# This route will generate an overall analysis of the quiz based on all attempts made by the user
@app.route('/quiz/<public_id>/overall_analysis')
@login_required
def overall_analysis(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    
    # Check if the user wants to force a re-analysis
    force_reanalyze = request.args.get('force_reanalyze', 'false').lower() == 'true'

    # If an analysis exists and we are NOT forcing a re-analysis, show the cached version
    if quiz.analysis_text and not force_reanalyze:
        return render_template('quiz_overall_analysis.html', quiz=quiz, analysis_html=quiz.analysis_text)

    # --- Otherwise, generate a new analysis ---
    if not quiz.attempts:
        return render_template('error.html', message="There are no attempts for this quiz yet, so an analysis cannot be generated.")

    # ... (The existing logic to tally incorrect answers and build the summary string remains the same) ...
    incorrect_counts = defaultdict(int)
    for attempt in quiz.attempts:
        for answer in attempt.answers:
            if not answer.is_correct:
                incorrect_counts[answer.question_id] += 1
    
    analysis_data_string = f"This quiz has been taken {len(quiz.attempts)} time(s).\n\n"
    analysis_data_string += "Here is a summary of the most frequently missed questions:\n"
    
    sorted_incorrect = sorted(incorrect_counts.items(), key=lambda item: item[1], reverse=True)
    
    for question_id, count in sorted_incorrect:
        question = db.session.get(Question, question_id)
        analysis_data_string += f"- Question: \"{question.content}\" was answered incorrectly {count} time(s).\n"
        analysis_data_string += f"  Correct Answer: \"{question.answer}\"\n"


    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = create_overall_analysis_prompt(analysis_data_string)
        response = model.generate_content(prompt)
        analysis_html = response.text
        
        # --- CRUCIAL STEP: Save the new analysis to the database ---
        quiz.analysis_text = analysis_html
        db.session.commit()
        
        return render_template('quiz_overall_analysis.html', quiz=quiz, analysis_html=analysis_html)

    except Exception as e:
        db.session.rollback() # Rollback in case of an error during generation
        logging.error(f"Error generating overall analysis for quiz {public_id}: {str(e)}")
        return render_template('error.html', message=f"An error occurred while generating the overall analysis. Error: {str(e)}")    

def create_overall_analysis_prompt(summary_data):
    """Creates a prompt for Gemini to analyze a whole class's performance."""
    return f"""
    You are an expert educational analyst. Your task is to analyze the overall performance of a group of students on a quiz and provide feedback to the teacher.
    Based on the following summary data, which highlights the most frequently incorrect answers, please provide a concise analysis.

    SUMMARY DATA:
    ---
    {summary_data}
    ---

    Your analysis should include three sections in markdown format:
    1.  **## Common Misconceptions**: Based on the questions that were frequently answered incorrectly, identify the key concepts or topics the students are struggling with as a group.
    2.  **## Potential Reasons**: Suggest possible reasons for these common struggles (e.g., the topic is complex, the question was ambiguous, more foundational knowledge is needed).
    3.  **## Recommendations for the Teacher**: Offer 2-3 specific, actionable recommendations for the whole class. For example, suggest a topic to re-teach, a different way to explain a concept, or a follow-up activity.

    Keep the tone professional, helpful, and focused on group-level educational improvement.
    """

# Dashboard route to view user's quizzes
@app.route('/')
@login_required
def index():
    quizzes = Quiz.query.options(db.joinedload(Quiz.attempts)).filter_by(user_id=current_user.id).order_by(Quiz.id.desc()).all()
    # CRUCIAL: Generate and pass the CSRF token
    csrf_token = generate_csrf()
    return render_template('dashboard.html', quizzes=quizzes, csrf_token=csrf_token)

#  Route to view attempts for a specific quiz
# This route will display all attempts made by the user for a specific quiz
@app.route('/quiz/<public_id>/attempts')
@login_required
def view_attempts(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    # Order the attempts by the most recent first
    attempts = sorted(quiz.attempts, key=lambda x: x.timestamp, reverse=True)
    return render_template('quiz_attempts.html', quiz=quiz, attempts=attempts)

# Route to delete a quiz
# This route will handle the deletion of a quiz and its associated questions and attempts
@app.route('/quiz/<public_id>/delete', methods=['POST'])
@login_required
def delete_quiz(public_id):
    # Ensure the user can only delete their own quizzes
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    
    # SQLAlchemy's cascade="all, delete-orphan" will handle deleting associated questions and attempts
    db.session.delete(quiz)
    db.session.commit()
    
    return redirect(url_for('index'))

@app.route('/terms')
def terms():
    """Renders the Terms of Use page."""
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    """Renders the Privacy Policy page."""
    return render_template('privacy.html')

@app.context_processor
def inject_now():
    """
    Injects a formatted string of the current date and time into templates.
    Usage in template: {{ now }}
    """
    return {'now': datetime.now().strftime('%Y-%m-%d %H:%M')}

# Add temporary debug route to app.py
@app.route('/debug-db')
def debug_db():
    quizzes = Quiz.query.all()
    output = []
    for quiz in quizzes:
        output.append(f"Quiz {quiz.id}: {quiz.title}")
        for q in quiz.questions:
            output.append(f" - Q{q.id}: {q.content[:50]}...")
    return "<br>".join(output)


@app.route('/test-db')
def test_db():
    try:
        # Get the database dialect (e.g., 'postgresql', 'mysql', 'sqlite')
        dialect = db.engine.dialect.name

        # Execute a raw SQL query to get the database server version
        # NOTE: 'SELECT version()' works for PostgreSQL and MySQL.
        # For SQLite, you might use 'SELECT sqlite_version()'.
        db_version_query = text('SELECT version()')
        db_version = db.session.execute(db_version_query).scalar()

        # Get a count of all records in the Question table
        question_count = db.session.query(Question).count()
        
        # Fetch the first Question record to ensure the table is readable
        first_question = db.session.query(Question).first()

        # Prepare the success response payload
        db_info = {
            "status": "success",
            "database_type": dialect,
            "database_version": db_version,
            "question_table_info": {
                "total_records": question_count,
                "sample_record": str(first_question) if first_question else "Table is empty"
            }
        }
        
        return jsonify(db_info)

    except Exception as e:
        # The error response is also formatted as JSON for consistency
        error_info = {
            "status": "error",
            "message": str(e)
        }
        return jsonify(error_info), 500
    
if __name__ == '__main__':
    app.run(debug=True)