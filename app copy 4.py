import io
import re
import sys
import uuid
import json
import logging
import os
import time
from datetime import datetime
from collections import defaultdict

# --- Standard Library Imports First ---
from flask import (
    Flask, current_app, flash, jsonify, render_template, 
    request, redirect, send_file, session, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash
from markupsafe import Markup

# --- Third-Party Imports ---
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from flask_migrate import Migrate
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, 
    login_required, current_user
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadTimeSignature
import google.generativeai as genai
import markdown
import pdfplumber
import qrcode
from bleach import clean

# ==============================================================================
# 1. APPLICATION SETUP AND CONFIGURATION
# ==============================================================================

# Load environment variables from .env file
load_dotenv()

# Initialize the Flask app
app = Flask(__name__)

# --- Load Configurations ---
# Secret key is essential for sessions, CSRF protection, etc.
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'a_default_secret_key_for_development')

# Database Configuration
if os.getenv('MYSQL_USER'):
    username = os.getenv('MYSQL_USER')
    password = os.getenv('MYSQL_PASSWORD')
    hostname = os.getenv('MYSQL_HOST')
    database = os.getenv('MYSQL_DB')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{username}:{password}@{hostname}/{database}"
else:
    # Fallback to SQLite if MySQL env vars are not set
    db_path = os.path.join(os.path.dirname(__file__), 'instance', 'questions.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Email Configuration
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() in ['true', '1', 't']
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_USERNAME')

# Gemini API Configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ==============================================================================
# 2. EXTENSION INITIALIZATION
# ==============================================================================

db = SQLAlchemy(app)
migrate = Migrate(app, db)
csrf = CSRFProtect(app)
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login' # Redirect to login if not authenticated

# ==============================================================================
# 3. DATABASE MODELS
# ==============================================================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    quizzes = db.relationship('Quiz', backref='creator', lazy=True, cascade="all, delete-orphan")

    def set_password(self, password):
        #self.password_hash = generate_password_hash(password)
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

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
        # Return the user_id directly.
        # The database query will be handled in the route itself.
        return user_id

class Quiz(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    title = db.Column(db.String(100))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    analysis_text = db.Column(db.Text, nullable=True)
    questions = db.relationship('Question', backref='quiz', cascade="all, delete-orphan")

    @property
    def total_score(self):
        return sum(q.marks for q in self.questions if q.marks is not None)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(50))
    bloom_level = db.Column(db.String(50))
    options = db.Column(db.Text)
    answer = db.Column(db.Text)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'))
    marks = db.Column(db.Integer, nullable=False, default=1)

class QuizAttempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_name = db.Column(db.String(100), nullable=False)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)
    total_score = db.Column(db.Integer, nullable=False)
    percentage = db.Column(db.Float, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    quiz = db.relationship('Quiz', backref=db.backref('attempts', lazy=True, cascade="all, delete-orphan"))
    answers = db.relationship('StudentAnswer', backref='attempt', lazy=True, cascade="all, delete-orphan")

class StudentAnswer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey('quiz_attempt.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    answer_text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    question = db.relationship('Question')

# ==============================================================================
# 4. FLASK-LOGIN USER LOADER
# ==============================================================================

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ==============================================================================
# 5. HELPER FUNCTIONS
# ==============================================================================

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
        
def generate_questions(material, types, count, bloom_level):
    model = genai.GenerativeModel('gemini-1.5-flash')
    type_string = ", ".join(types)

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
        return response.text
    except Exception as e:
        logging.error(f"Gemini API Error: {str(e)}")
        return "An error occurred while generating questions. Please try again later.", 500
        
def parse_questions(questions_text):
    """
    Parses a JSON string from Gemini into a list of question dictionaries.
    """
    try:
        json_match = re.search(r'\[.*\]|\{.*\}', questions_text, re.DOTALL)
        if not json_match:
            logging.error(f"Could not find valid JSON in AI response: {questions_text}")
            return []

        clean_json_str = json_match.group(0)
        parsed_data = json.loads(clean_json_str)

        questions_for_app = []
        for q in parsed_data:
            new_q = {
                'type': q.get('type'),
                'marks': q.get('marks'),
                'bloom_level': q.get('bloom_level'),
                'text': q.get('text'),
                'answer': q.get('answer'),
                'options': '\n'.join(q.get('options', []))
            }
            questions_for_app.append(new_q)

        return questions_for_app

    except json.JSONDecodeError as e:
        logging.error(f"JSON Parsing Error: {e}\nRaw Response was:\n{questions_text}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred in parse_questions: {e}")
        return []

def send_reset_email(user):
    token = user.get_reset_token()
    msg = Message('Password Reset Request', recipients=[user.email])
    msg.html = render_template('reset_password_email.html', user=user, token=token)
    try:
        mail.send(msg)
    except Exception as e:
        app.logger.error(f"Failed to send email: {e}")
        raise

def grade_short_answer_with_gemini(correct_answer, student_answer):
    """Sends answers to Gemini for grading and parses the JSON response."""
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        You are an expert examiner grading a short-answer question.
        Determine if the student's answer is semantically and factually correct based on the answer key.
        The student's answer can be a subset of the key, as long as it is accurate.

        **Answer Key:** "{correct_answer}"
        **Student's Answer:** "{student_answer}"

        Respond ONLY in JSON format with one key: "is_correct" (boolean).
        """
        
        response = model.generate_content(prompt)
        cleaned_response = response.text.strip().replace('```json', '').replace('```', '')
        grade_data = json.loads(cleaned_response)
        return grade_data.get('is_correct', False)

    except Exception as e:
        logging.error(f"Gemini API grading error: {str(e)}")
        return False

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

# ==============================================================================
# 6. ROUTES AND VIEW FUNCTIONS
# ==============================================================================

@app.route('/')
@login_required
def index():
    quizzes = Quiz.query.filter_by(user_id=current_user.id).order_by(Quiz.id.desc()).all()
    csrf_token = generate_csrf()
    return render_template('dashboard.html', quizzes=quizzes, csrf_token=csrf_token)

@app.route('/create-quiz', methods=['GET', 'POST'])
@login_required
def create_quiz():
    if request.method == 'POST':
        course_material = ""
        form_data = request.form
        
        if 'pdf_file' in request.files:
            pdf_file = request.files['pdf_file']
            if pdf_file.filename != '':
                if not pdf_file.filename.lower().endswith('.pdf'):
                    flash("Invalid file type. Please upload a PDF.", 'danger')
                    return redirect(url_for('index'))
                
                course_material = extract_text_from_pdf(pdf_file.stream)
                
                if course_material is None:
                    flash("Could not extract text from the PDF. The file might be corrupted or image-based.", 'danger')
                    return redirect(url_for('index'))
        
        if not course_material:
            course_material = form_data.get('course_material')

        if not course_material.strip():
            flash("No course material provided. Please paste text or upload a PDF.", 'danger')
            return redirect(url_for('index'))

        if not form_data.getlist('question_types'):
            flash("Please select at least one question type.", 'danger')
            return redirect(url_for('index'))

        sanitized_course_material = clean(course_material)
        question_types = form_data.getlist('question_types')
        num_questions = int(form_data.get('num_questions', 2))
        bloom_level = form_data.get('bloom_level')
        
        questions_text = generate_questions(sanitized_course_material, question_types, num_questions, bloom_level)
        
        questions_list_for_display = parse_questions(questions_text)

        question_type_order = ["MCQ", "True/False", "Fill-in-the-Blank", "Short Answer"]
        sorted_questions = sorted(
            questions_list_for_display,
            key=lambda q: question_type_order.index(q.get('type', '')) if q.get('type') in question_type_order else len(question_type_order)
        )
        
        session['generated_questions'] = questions_text
        csrf_token = generate_csrf()
        
        return render_template(
            'results.html', 
            questions_list=sorted_questions,
            raw_questions_text=questions_text,
            csrf_token=csrf_token
        )

    csrf_token = generate_csrf()
    return render_template('index.html', csrf_token=csrf_token)

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
                bloom_level=q_data.get('bloom_level'),
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

# --- Authentication and User Management Routes ---

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
        
        user_by_username = User.query.filter_by(username=username).first()
        if user_by_username:
            flash('Username already exists. Please choose a different one.', 'danger')
            return redirect(url_for('register'))
        
        user_by_email = User.query.filter_by(email=email).first()
        if user_by_email:
            flash('Email address is already registered.', 'danger')
            return redirect(url_for('register'))
            
        if password != confirm_password:
            flash('Passwords do not match. Please try again.', 'danger')
            return redirect(url_for('register'))
            
        if len(password) < 8:
            flash('Password must be at least 8 characters long.', 'danger')
            return redirect(url_for('register'))

        new_user = User(username=username, email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Account created successfully! You are now logged in.', 'success')
        login_user(new_user)
        return redirect(url_for('index'))
    csrf_token = generate_csrf()
    return render_template('register.html', csrf_token=csrf_token)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash('Invalid username or password.', 'danger')
            return redirect(url_for('login'))
        
        login_user(user)
        return redirect(url_for('index'))
    csrf_token = generate_csrf()
    return render_template('login.html', csrf_token=csrf_token)
    
@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            send_reset_email(user)
        flash('If an account with that email exists, a password reset link has been sent.', 'success')
        return redirect(url_for('login'))
    csrf_token = generate_csrf()
    return render_template('forgot_password.html', csrf_token=csrf_token)

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    # This correctly receives the user_id
    user_id = User.verify_reset_token(token)
    
    if not user_id:
        flash('That is an invalid or expired token.', 'danger')
        return redirect(url_for('forgot_password'))
    
    # FIX user not found: Use the modern, session-aware method to get the user
    user = db.session.get(User, user_id)
    
    if not user:
        flash('User not found.', 'danger')
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

        # This is updating the "live" user object from the database
        user.set_password(password)
        db.session.commit()
        
        flash('Your password has been updated! You are now able to log in.', 'success')
        return redirect(url_for('login'))

    csrf_token = generate_csrf()
    return render_template('reset_password.html', token=token, csrf_token=csrf_token)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not current_user.check_password(old_password):
            flash('Your old password was incorrect. Please try again.', 'danger')
            return redirect(url_for('change_password'))

        if new_password != confirm_password:
            flash('The new passwords do not match.', 'danger')
            return redirect(url_for('change_password'))
            
        if len(new_password) < 8:
            flash('Your new password must be at least 8 characters long.', 'danger')
            return redirect(url_for('change_password'))

        current_user.set_password(new_password)
        db.session.commit()
        flash('Your password has been updated successfully!', 'success')
        return redirect(url_for('index'))

    csrf_token = generate_csrf()
    return render_template('change_password.html', csrf_token=csrf_token)

# --- Quiz Interaction and Management Routes ---

@app.route('/quiz/<public_id>')
@login_required
def view_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    question_type_order = ["MCQ", "True/False", "Fill-in-the-Blank", "Short Answer"]
    sorted_questions = sorted(
        quiz.questions,
        key=lambda q: question_type_order.index(q.question_type) if q.question_type in question_type_order else len(question_type_order)
    )
    return render_template('quiz.html', quiz=quiz, questions=sorted_questions)

@app.route('/quiz/<public_id>/take', methods=['GET'])
def take_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    question_type_order = ["MCQ", "True/False", "Fill-in-the-Blank", "Short Answer"]
    sorted_questions = sorted(
        quiz.questions,
        key=lambda q: question_type_order.index(q.question_type) if q.question_type in question_type_order else len(question_type_order)
    )
    csrf_token = generate_csrf()
    return render_template('take_quiz.html', quiz=quiz, questions=sorted_questions, csrf_token=csrf_token)

@app.route('/quiz/<public_id>/submit', methods=['POST'])
def submit_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    questions = quiz.questions
    
    score = 0
    total_score = quiz.total_score
    results_for_template = []

    question_type_order = ["MCQ", "True/False", "Fill-in-the-Blank", "Short Answer"]
    sorted_questions = sorted(
        questions,
        key=lambda q: question_type_order.index(q.question_type) if q.question_type in question_type_order else len(question_type_order)
    )
    
    try:
        student_name = request.form.get('student_name', 'Anonymous')
        new_attempt = QuizAttempt(
            quiz_id=quiz.id,
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
            
            student_answer_record = StudentAnswer(
                answer_text=student_answer_text,
                is_correct=is_correct,
                question=question
            )
            new_attempt.answers.append(student_answer_record)

            results_for_template.append({
                'question': question,
                'student_answer': student_answer_text,
                'correct_answer': correct_answer_text,
                'is_correct': is_correct
            })

        if total_score > 0:
            percentage = round((score / total_score) * 100, 2)
        else:
            percentage = 0
            
        new_attempt.score = score
        new_attempt.percentage = percentage
        
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

@app.route('/quiz/<public_id>/attempts')
@login_required
def view_attempts(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    attempts = sorted(quiz.attempts, key=lambda x: x.timestamp, reverse=True)
    return render_template('quiz_attempts.html', quiz=quiz, attempts=attempts)

@app.route('/quiz/<public_id>/delete', methods=['POST'])
@login_required
def delete_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    db.session.delete(quiz)
    db.session.commit()
    flash('Quiz deleted successfully.', 'success')
    return redirect(url_for('index'))

@app.route('/quiz/<public_id>/overall_analysis')
@login_required
def overall_analysis(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id, user_id=current_user.id).first_or_404()
    force_reanalyze = request.args.get('force_reanalyze', 'false').lower() == 'true'

    if quiz.analysis_text and not force_reanalyze:
        return render_template('quiz_overall_analysis.html', quiz=quiz, analysis_html=quiz.analysis_text)

    if not quiz.attempts:
        return render_template('error.html', message="There are no attempts for this quiz yet, so an analysis cannot be generated.")

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
        
        quiz.analysis_text = analysis_html
        db.session.commit()
        
        return render_template('quiz_overall_analysis.html', quiz=quiz, analysis_html=analysis_html)

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error generating overall analysis for quiz {public_id}: {str(e)}")
        return render_template('error.html', message=f"An error occurred while generating the overall analysis. Error: {str(e)}")    

# --- Static Pages and Utility Routes ---

@app.route('/terms')
def terms():
    """Renders the Terms of Use page."""
    return render_template('terms.html')

@app.route('/privacy')
def privacy():
    """Renders the Privacy Policy page."""
    return render_template('privacy.html')

@app.route('/quiz/<public_id>/qr')
def quiz_qr_code(public_id):
    """Generates a QR code for the quiz link."""
    quiz_url = url_for('take_quiz', public_id=public_id, _external=True)
    img = qrcode.make(quiz_url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# ==============================================================================
# 7. ERROR HANDLERS AND OTHER APP-WIDE CONFIGURATIONS
# ==============================================================================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', message="Sorry, the page you are looking for does not exist."), 404

@app.errorhandler(OperationalError)
def handle_db_connection_error(e):
    app.logger.error(f"Database Connection Error: {e}", exc_info=True)
    db.session.rollback()
    message = "We're currently experiencing technical difficulties. Please try again later."
    return render_template('error.html', message=message), 503

@app.context_processor
def inject_now():
    return {'now': datetime.now().strftime('%Y-%m-%d %H:%M')}

@app.template_filter('markdown')
def markdown_filter(s):
    return Markup(markdown.markdown(s))
    
# ==============================================================================
# 8. MAIN EXECUTION BLOCK
# ==============================================================================

if __name__ == '__main__':
    app.run(debug=True)

