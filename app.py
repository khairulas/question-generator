from flask import Flask, render_template, request, redirect, url_for
import requests
from dotenv import load_dotenv
import google.generativeai as genai
from bleach import clean
import logging
import os
import time
from collections import defaultdict
load_dotenv()

app = Flask(__name__)
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

@app.route('/', methods=['GET', 'POST'])
def index():
    user_ip = request.remote_addr
    if is_rate_limited(user_ip):
        return "Too many requests", 429
    if request.method == 'POST':
        errors, course_material, question_types, num_questions, bloom_level = validate_input(request.form)

        if errors:
            return render_template('index.html', errors=errors, form=request.form), 400

        sanitized_course_material = clean(course_material)  # Sanitize input
        questions = generate_questions(sanitized_course_material, question_types, num_questions, bloom_level)
        return render_template('results.html', questions=questions)

    return render_template('index.html', errors={})

def generate_questions(material, types, count, bloom_level):
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = f"""
    You are an expert educator. Generate {count} questions with these rules:
    1. Format: {types} question types
    - For MCQ: Options A-D + "Answer: X"
    2. Bloom's level: {bloom_level}
    3. Material: {material}
    4. Ensure the questions are clear and concise.
    5. Provide suggested answers for each question.
    """
    try:
        response = model.generate_content(prompt)
        questions = response.text
        sanitized_questions = clean(questions)  # Sanitize Gemini output
        return sanitized_questions
    except Exception as e:
        logging.error(f"Gemini API Error: {str(e)}")
        return "An error occurred while generating questions. Please try again later.", 500

if __name__ == '__main__':
    app.run(debug=False)