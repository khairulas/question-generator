Of course. Here is an updated `README.md` that reflects the application's current state, including the new name, deployment on PythonAnywhere, and more detailed setup instructions.

-----

# AI Quiz Generator

**Instantly transform any text into intelligent educational quizzes with this AI-powered web application.**

-----

## Live Demo

The application is deployed on PythonAnywhere:

**[https://aiquiz.pythonanywhere.com/](https://www.google.com/search?q=https://aiquiz.pythonanywhere.com/)**

## App Screenshot

*(This is where your `static/screenshot.png` would be displayed)*

## About The Project

This web application harnesses the power of Google's Gemini AI to revolutionize the creation of educational assessments. Designed for educators and trainers, this platform transforms any text-based content—pasted directly or uploaded as a PDF—into a comprehensive set of custom-tailored quiz questions.

Users can specify desired question types (Multiple Choice, True/False, Short Answer), quantity, and cognitive complexity based on Bloom's Taxonomy. The app features secure user accounts, a dashboard for quiz management, and intelligent, AI-powered grading for short-answer questions.

## Key Features

  * **🤖 AI-Powered Question Generation:** Creates questions from any provided text or PDF file using the Gemini API.
  * **📝 Customizable Quizzes:** Control question types, quantity, and Bloom's Taxonomy level.
  * **🧠 Intelligent Short-Answer Grading:** Uses the Gemini API to evaluate short answers based on semantic meaning.
  * **📊 Performance Analytics:** Instructors can view overall class performance on a quiz to identify common misconceptions and receive AI-generated recommendations.
  * **🔐 Secure User Accounts:** Full user registration and login system with password hashing, password resets, and CSRF protection.
  * **🎛️ Instructor Dashboard:** View, manage, edit, share, and delete quizzes.
  * **🔗 Secure & Shareable Links:** Each quiz has a unique, unguessable URL and a scannable QR code for easy sharing.
  * **🛡️ Rate Limiting:** Protects the application from spam and overuse of the AI API.
  * **📱 Responsive Design:** The interface is optimized for a seamless experience on both desktop and mobile devices.

## Technologies Used

  * **Backend:** Python, Flask, Gunicorn
  * **Database:** SQLAlchemy, PostgreSQL (for production), SQLite (for development)
  * **AI Integration:** Google Gemini API
  * **Frontend:** HTML5, CSS3, Jinja2
  * **Flask Extensions:** Flask-Login, Flask-Migrate, Flask-Mail, Flask-Limiter, Flask-WTF (for CSRF)
  * **Caching/Rate Limiting:** Redis
  * **Deployment:** PythonAnywhere

## Local Installation & Setup

To run this project locally, follow these steps:

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/khairulas/question-generator.git
    cd question-generator
    ```

2.  **Create and activate a virtual environment:**

      * On Windows:
        ```bash
        python -m venv venv
        .\venv\Scripts\activate
        ```
      * On macOS/Linux:
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```

3.  **Install the dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Set up your environment variables:**

      * Create a file named `.env` in the root directory.
      * Add your configuration details to this file. This is a critical step for the app to function.
        ```
        # Flask App Secret
        FLASK_SECRET_KEY='your_super_secret_key'

        # Gemini API Key
        GEMINI_API_KEY='your_gemini_api_key'

        # Email Configuration (Example for Gmail)
        MAIL_SERVER='smtp.gmail.com'
        MAIL_PORT=587
        MAIL_USE_TLS=True
        MAIL_USERNAME='your_email@gmail.com'
        MAIL_PASSWORD='your_gmail_app_password'
        ```

5.  **Initialize the database:**

      * If you are running the app for the first time, you need to create the database migrations.
        ```bash
        flask db init
        flask db migrate -m "Initial migration"
        ```
      * Apply the migrations to create your database schema:
        ```bash
        flask db upgrade
        ```

6.  **Run the application:**

    ```bash
    flask run
    ```

    Open your browser and navigate to `http://127.0.0.1:5000`.

## Deployment

This application is configured for deployment on PythonAnywhere. The key steps involve:

1.  Cloning the repository to the PythonAnywhere server.
2.  Setting up a new web app with "Manual Configuration".
3.  Creating a virtual environment and installing packages from `requirements.txt`.
4.  Configuring the WSGI file to point to the Flask app instance.
5.  Adding all necessary environment variables to the "Environment variables" section in the "Web" tab.

## License

This project is licensed under the MIT License.