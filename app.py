import io
import re
import sys
import uuid
import json
from flask import Flask, flash, jsonify, render_template, request, redirect, send_file, session, url_for
from flask_migrate import Migrate
from flask import Flask, render_template
from flask_wtf.csrf import CSRFProtect, generate_csrf
import qrcode
import requests
from dotenv import load_dotenv
import google.generativeai as genai
from bleach import clean
import logging
import os
import time
from collections import defaultdict
load_dotenv()
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from markupsafe import Markup
import markdown

app = Flask(__name__)

# --- CORRECTED CONFIGURATION ORDER ---
# 1. Load the secret key from the environment first.
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')

# 2. Configure other app settings.
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.dirname(__file__), 'instance/questions.db')
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 3. Initialize extensions AFTER the main config is set.
csrf = CSRFProtect(app)
db = SQLAlchemy(app)
migrate = Migrate(app, db) 
# --- END OF CORRECTION ---


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
    password_hash = db.Column(db.String(128))
    quizzes = db.relationship('Quiz', backref='creator', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
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

# Create tables (run once)
with app.app_context():
    db.create_all()

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

@app.route('/create-quiz', methods=['GET', 'POST'])
@login_required
def create_quiz():
    user_ip = request.remote_addr
    if is_rate_limited(user_ip):
        return "Too many requests", 429
        
    if request.method == 'POST':
        errors, course_material, question_types, num_questions, bloom_level = validate_input(request.form)
        
        if errors:
            return render_template('index.html', errors=errors, form=request.form), 400

        sanitized_course_material = clean(course_material)
        # Pass the selected 'question_types' to the function
        questions_text = generate_questions(sanitized_course_material, question_types, num_questions, bloom_level)
        session['bloom_level'] = bloom_level
        session['generated_questions'] = questions_text
        csrf_token = generate_csrf()
        return render_template('results.html', questions=questions_text, csrf_token=csrf_token)
    csrf_token = generate_csrf()
    return render_template('index.html', errors={}, csrf_token=csrf_token)


# In app.py

def generate_questions(material, types, count, bloom_level):
    model = genai.GenerativeModel('gemini-1.5-flash')

    # --- Start of new dynamic prompt logic ---
    type_string = ", ".join(types)
    prompt_examples = ""

    if "MCQ" in types:
        prompt_examples += """
    **Question Example (MCQ):**
    [Type: MCQ] What is a primary challenge of big data?
    A) Small data volume
    B) Lack of data sources
    C) The large volume and variety of data
    D) Ease of processing
    Answer: C
    """

    if "True/False" in types:
        prompt_examples += """
    **Question Example (True/False):**
    [Type: True/False] Most data generated today is structured.
    Answer: False
    """

    if "Short Answer" in types:
        prompt_examples += """
    **Question Example (Short Answer):**
    [Type: Short Answer] Name one characteristic of big data.
    Answer: Volume
    """
    # --- End of new dynamic prompt logic ---

    prompt = f"""
    Generate exactly {count} questions of the following types: {type_string}.
    Use the provided course material and adhere to the Bloom's Taxonomy level of '{bloom_level}'.

    MARKING RUBRIC:
    - Assign marks based on the question type and its complexity.
    - True/False questions should be worth 1-2 marks.
    - Multiple Choice (MCQ) questions should be worth 2-3 marks.
    - Short Answer questions should be worth 3-5 marks.
    - Questions at a higher Bloom's level like 'Analyzing' or 'Evaluating' should be assigned more marks within their range.

    STRICT FORMATTING RULES:
    1. Begin each question with '**Question X:**'.
    2. Always include the type in brackets, like '[Type: MCQ]'.
    3. Always include the suggested marks based on the rubric, like '[Marks: 4]'.
    4. Always include the Bloom's level, like '[Bloom: Remembering]'.
    5. For MCQs, provide four options labeled A), B), C), and D).
    6. Each question must end with a single line: 'Answer: [Correct Answer]'.
    7. Separate each complete question block with a blank line.
    8. Do not use any HTML tags like <br> in your output.
    9. Make questions standalone. Do not use phrases like "According to the text", "Based on the provided text", or "mentioned in the text".

    COURSE MATERIAL:
    "{material}"
    """
    try:
        response = model.generate_content(prompt)
        questions = response.text
        sanitized_questions = clean(questions)
        return sanitized_questions
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
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        # Check if username already exists
        user = User.query.filter_by(username=username).first()
        if user:
            flash('Username already exists. Please choose a different one.', 'danger')
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
        new_user = User(username=username)
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
    """Parse Gemini's output, with correct type, marks, and bloom level detection."""
    questions = []
    cleaned_text = re.sub(r'<br\s*/?>', '\n', str(questions_text), flags=re.IGNORECASE)
    blocks = cleaned_text.split('**Question')[1:]

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        
        current = {}
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        first_line = lines[0] # The line with the tags

        # --- START OF NEW PARSING LOGIC ---
        # 1. Extract Type from the first line of the block
        question_type = 'Short Answer'
        type_match = re.search(r'\[Type:\s*(.*?)\]', first_line, re.IGNORECASE)
        if type_match:
            found_type = type_match.group(1).strip()
            if 'mcq' in found_type.lower():
                question_type = 'MCQ'
            elif 'true/false' in found_type.lower():
                question_type = 'True/False'
        
        # 2. Extract Marks from the first line
        marks = 5
        marks_match = re.search(r'\[Marks:\s*(\d+)\]', first_line, re.IGNORECASE)
        if marks_match:
            marks = int(marks_match.group(1))

        # 3. Extract Bloom's Level from the first line
        bloom_level = 'Remembering'
        bloom_match = re.search(r'\[Bloom:\s*(.*?)\]', first_line, re.IGNORECASE)
        if bloom_match:
            bloom_level = bloom_match.group(1).strip()
            
        current['type'] = question_type
        current['marks'] = marks
        current['bloom_level'] = bloom_level
        # --- END OF NEW PARSING LOGIC ---

        # Separate the actual question content, options, and answer
        content_lines = lines[1:]
        question_text_lines = []
        option_lines = []
        answer_line = None

        for line in content_lines:
            if line.lower().startswith('answer:'):
                answer_line = line
            elif re.match(r'^[A-D]\)', line):
                option_lines.append(line)
            else:
                question_text_lines.append(line)

        if not answer_line:
            continue
            
        current['text'] = ' '.join(question_text_lines)
        current['answer'] = answer_line.split(':', 1)[1].strip()

        if question_type == 'MCQ':
            current['options'] = '\n'.join(option_lines)
        else:
            current['options'] = ''

        if current.get('text') and current.get('answer'):
            questions.append(current)
            
    return questions

# Change the route from <int:quiz_id> to <public_id>
# This allows us to use the public_id instead of the primary key
@app.route('/quiz/<public_id>')
@login_required
def view_quiz(public_id):
    # Find the quiz by its public_id instead of its primary key
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    return render_template('quiz.html', quiz=quiz, questions=quiz.questions)

# Route to take the quiz
#  This route will render the quiz form for the user to fill out
@app.route('/quiz/<public_id>/take', methods=['GET'])
def take_quiz(public_id):
    quiz = Quiz.query.filter_by(public_id=public_id).first_or_404()
    return render_template('take_quiz.html', quiz=quiz)

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

        for question in questions:
            student_answer_text = request.form.get(f'question_{question.id}', 'Not Answered')
            correct_answer_text = question.answer.strip()
            
            is_correct = False
            if question.question_type in ['MCQ', 'True/False']:
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
        db.session.query(Question).first()
        return "Database working"
    except Exception as e:
        return str(e), 500
    
if __name__ == '__main__':
    app.run(debug=False)