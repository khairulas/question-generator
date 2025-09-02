# Valence Learning: AI-Powered Quiz Generator

**Valence Learning: Instantly transform any text into intelligent educational quizzes.**

---

## Live Demo

[Link to your live application on Render] `https://iquiz-pxqd.onrender.com/`

## App Screenshot

![App Screenshot](static/screenshot.png)

## About The Project

Valence Learning is a web application that harnesses the power of Google's Gemini AI to revolutionize the creation of educational assessments. Designed for educators and trainers, this platform transforms any text-based content into a comprehensive set of custom-tailored quiz questions.

Users can specify desired question types (Multiple Choice, True/False, Short Answer), quantity, and cognitive complexity based on Bloom's Taxonomy. The app features secure user accounts, a dashboard for quiz management, and intelligent, AI-powered grading for short-answer questions.

## Key Features

* **🤖 AI-Powered Question Generation:** Creates questions from any provided text using the Gemini API.
* **📝 Customizable Quizzes:** Control question types, quantity, and Bloom's Taxonomy level.
* **🧠 Intelligent Short-Answer Grading:** Uses the Gemini API to evaluate short answers based on semantic meaning.
* **📊 Performance Analytics:** Instructors can view overall class performance on a quiz to identify common misconceptions and receive AI-generated recommendations.
* **🔐 Secure User Accounts:** Full user registration and login system with password hashing and CSRF protection.
* **🎛️ Instructor Dashboard:** View, manage, share, and delete quizzes.
* **🔗 Secure & Shareable Links:** Each quiz has a unique, unguessable URL and a scannable QR code for easy sharing.
* **📱 Responsive Design:** The interface is optimized for a seamless experience on both desktop and mobile devices.

## Technologies Used

* **Backend:** Python, Flask, Flask-SQLAlchemy, Flask-Login, Flask-Migrate
* **Frontend:** HTML5, CSS3 (with Flexbox for responsive design), Jinja2
* **AI Integration:** Google Gemini API
* **Database:** PostgreSQL (for production), SQLite (for development)
* **Deployment:** Render, Gunicorn

## Installation & Setup

To run this project locally, follow these steps:

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/your-username/iquiz.git](https://github.com/your-username/iquiz.git)
    cd iquiz
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv .venv
    .\.venv\Scripts\activate
    ```

3.  **Install the dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up your environment variables:**
    * Create a file named `.env` in the root directory.
    * Add your secret keys to this file:
        ```
        FLASK_SECRET_KEY='your_super_secret_key'
        GEMINI_API_KEY='your_gemini_api_key'
        ```

5.  **Initialize and upgrade the database:**
    ```bash
    flask db upgrade
    ```

6.  **Run the application:**
    ```bash
    python app.py
    ```

## License

This project is licensed under the MIT License.