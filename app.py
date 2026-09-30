from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from dbconnect import get_connection
from ultralytics import YOLO
import os
import zipfile
import shutil
import threading

app = Flask(__name__)
app.secret_key = "plant-health-secret-key"

app.config["UPLOAD_FOLDER"] = "static/uploads"

model = YOLO("models/best.pt")

# Store training status and results
training_status = {}
ai_training_data = {}
training_threads = {}

@app.route("/")
def home():
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return redirect(url_for("login"))

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM users WHERE email = %s",
                    (email,)
                )
                user = cursor.fetchone()

            if user and check_password_hash(user["password"], password):
                session["user_id"] = user["user_id"]
                session["user_name"] = user["name"]

                return redirect(url_for("dashboard"))

            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        finally:
            connection.close()

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            flash("All fields are required.", "error")
            return redirect(url_for("register"))

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT user_id FROM users WHERE email = %s",
                    (email,)
                )

                if cursor.fetchone():
                    flash("Email is already registered.", "error")
                    return redirect(url_for("register"))

                cursor.execute(
                    """
                    INSERT INTO users (name, email, password)
                    VALUES (%s, %s, %s)
                    """,
                    (name, email, hashed_password)
                )

            connection.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))

        except Exception as error:
            connection.rollback()
            flash(f"Registration failed: {error}", "error")
            return redirect(url_for("register"))

        finally:
            connection.close()

    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/about")
def about():

    return render_template("about.html")

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:

            # Total detections
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM classifications
                WHERE user_id = %s
                """,
                (session["user_id"],)
            )
            total_detections = cursor.fetchone()["total"]

            # Total healthy plants
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM classifications
                WHERE user_id = %s
                AND health_status = 'Healthy'
                """,
                (session["user_id"],)
            )
            healthy_plants = cursor.fetchone()["total"]

            # Total unhealthy plants
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM classifications
                WHERE user_id = %s
                AND health_status = 'Unhealthy'
                """,
                (session["user_id"],)
            )
            unhealthy_plants = cursor.fetchone()["total"]

            # Unhealthy plants for current month
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM classifications
                WHERE user_id = %s
                AND health_status = 'Unhealthy'
                AND YEAR(classification_date) = YEAR(CURDATE())
                AND MONTH(classification_date) = MONTH(CURDATE())
                """,
                (session["user_id"],)
            )
            monthly_unhealthy = cursor.fetchone()["total"]

            # Recent detections
            cursor.execute(
                """
                SELECT
                    p.plant_name,
                    c.health_status,
                    c.disease_name,
                    c.confidence,
                    c.classification_date
                FROM classifications c
                JOIN plants p ON c.plant_id = p.plant_id
                WHERE c.user_id = %s
                ORDER BY c.classification_date DESC
                LIMIT 5
                """,
                (session["user_id"],)
            )
            recent_detections = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "dashboard.html",
        user_name=session["user_name"],
        total_detections=total_detections,
        healthy_plants=healthy_plants,
        unhealthy_plants=unhealthy_plants,
        monthly_unhealthy=monthly_unhealthy,
        recent_detections=recent_detections
    )

@app.route("/detect", methods=["GET", "POST"])
def detect():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        image = request.files.get("image")

        if not image or image.filename == "":
            flash("Please select an image.", "error")
            return redirect(url_for("detect"))

        filename = image.filename
        image_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        image.save(image_path)

        results = model.predict(
            source=image_path,
            imgsz=224
        )

        result = results[0]

        class_id = result.probs.top1
        class_name = result.names[class_id]
        confidence = float(result.probs.top1conf) * 100

        health_status = (
            "Healthy"
            if "healthy" in class_name.lower()
            else "Unhealthy"
        )

        if class_name.startswith("Pepper"):
            plant_name = "Pepper Bell"
        elif class_name.startswith("Potato"):
            plant_name = "Potato"
        else:
            plant_name = "Tomato"

        if health_status == "Healthy":
            disease_name = "-"
        else:
            disease_name = class_name

            disease_name = disease_name.replace(
                "Pepper__bell___", ""
            )
            disease_name = disease_name.replace(
                "Potato___", ""
            )
            disease_name = disease_name.replace(
                "Tomato__", ""
            )
            disease_name = disease_name.replace(
                "Tomato_", ""
            )

            disease_name = disease_name.replace("_", " ").strip().title()

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT plant_id
                    FROM plants
                    WHERE plant_name = %s
                    """,
                    (plant_name,)
                )

                plant = cursor.fetchone()

                if not plant:
                    flash("Plant record was not found.", "error")
                    return redirect(url_for("detect"))

                cursor.execute(
                    """
                    INSERT INTO classifications
                    (
                        user_id,
                        plant_id,
                        image_path,
                        health_status,
                        disease_name,
                        confidence
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        session["user_id"],
                        plant["plant_id"],
                        filename,
                        health_status,
                        disease_name,
                        confidence
                    )
                )

                classification_id = cursor.lastrowid

            connection.commit()

        except Exception as error:
            connection.rollback()
            flash(f"Failed to save detection result: {error}", "error")
            return redirect(url_for("detect"))

        finally:
            connection.close()

        return render_template(
            "detect.html",
            plant_name=plant_name,
            health_status=health_status,
            disease_name=disease_name,
            confidence=f"{confidence:.2f}",
            classification_id=classification_id,
            image_filename=filename
        )

    return render_template("detect.html")

@app.route("/reports")
def reports():
    if "user_id" not in session:
        return redirect(url_for("login"))

    search = request.args.get("search", "").strip()

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            if search:
                cursor.execute(
                    """
                    SELECT
                        c.classification_id,
                        p.plant_name,
                        c.image_path,
                        c.health_status,
                        c.disease_name,
                        c.confidence,
                        c.classification_date
                    FROM classifications c
                    JOIN plants p ON c.plant_id = p.plant_id
                    WHERE c.user_id = %s
                    AND (
                        p.plant_name LIKE %s
                        OR c.health_status LIKE %s
                        OR c.disease_name LIKE %s
                    )
                    ORDER BY c.classification_date DESC
                    """,
                    (
                        session["user_id"],
                        f"%{search}%",
                        f"%{search}%",
                        f"%{search}%"
                    )
                )
            else:
                cursor.execute(
                    """
                    SELECT
                        c.classification_id,
                        p.plant_name,
                        c.image_path,
                        c.health_status,
                        c.disease_name,
                        c.confidence,
                        c.classification_date
                    FROM classifications c
                    JOIN plants p ON c.plant_id = p.plant_id
                    WHERE c.user_id = %s
                    ORDER BY c.classification_date DESC
                    """,
                    (session["user_id"],)
                )

            report_data = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "report.html",
        reports=report_data,
        search=search
    )

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Email and password are required.", "error")
            return redirect(url_for("admin_login"))

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM admins WHERE email = %s",
                    (email,)
                )
                admin = cursor.fetchone()

            if admin and check_password_hash(admin["password"], password):
                session.clear()
                session["admin_id"] = admin["admin_id"]
                session["admin_name"] = admin["name"]

                return redirect(url_for("admin_dashboard"))

            flash("Invalid admin email or password.", "error")
            return redirect(url_for("admin_login"))

        finally:
            connection.close()

    return render_template("admin_login.html")


@app.route("/admin-dashboard")
def admin_dashboard():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    return render_template(
        "admin_dashboard.html",
        admin_name=session["admin_name"]
    )


@app.route("/admin-logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/admin-reports")
def admin_reports():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    search = request.args.get("search", "").strip()

    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            if search:

                cursor.execute(
                    """
                    SELECT
                        c.classification_id,
                        c.image_path,
                        u.name AS user_name,
                        p.plant_name,
                        c.health_status,
                        c.disease_name,
                        c.confidence,
                        c.classification_date

                    FROM classifications c

                    JOIN users u
                        ON c.user_id = u.user_id

                    JOIN plants p
                        ON c.plant_id = p.plant_id

                    WHERE
                        u.name LIKE %s
                        OR p.plant_name LIKE %s
                        OR c.health_status LIKE %s
                        OR c.disease_name LIKE %s

                    ORDER BY c.classification_date DESC
                    """,

                    (
                        f"%{search}%",
                        f"%{search}%",
                        f"%{search}%",
                        f"%{search}%"
                    )
                )

            else:

                cursor.execute(
                    """
                    SELECT
                        c.classification_id,
                        c.image_path,
                        u.name AS user_name,
                        p.plant_name,
                        c.health_status,
                        c.disease_name,
                        c.confidence,
                        c.classification_date

                    FROM classifications c

                    JOIN users u
                        ON c.user_id = u.user_id

                    JOIN plants p
                        ON c.plant_id = p.plant_id

                    ORDER BY c.classification_date DESC
                    """
                )

            report_data = cursor.fetchall()

    finally:

        connection.close()


    return render_template(
        "admin_reports.html",

        reports=report_data,

        search=search
    )
    
@app.route("/admin-reports/delete/<int:classification_id>", methods=["POST"])
def delete_report(classification_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM reports
                WHERE classification_id = %s
                """,
                (classification_id,)
            )

            cursor.execute(
                """
                DELETE FROM classifications
                WHERE classification_id = %s
                """,
                (classification_id,)
            )

        connection.commit()
        flash("Report deleted successfully.", "success")

    except Exception as error:
        connection.rollback()
        flash(f"Failed to delete report: {error}", "error")

    finally:
        connection.close()

    return redirect(url_for("admin_reports"))

@app.route("/admin-plants")
def admin_plants():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    search = request.args.get("search", "").strip()

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            if search:
                cursor.execute(
                    """
                    SELECT plant_id, plant_name, description
                    FROM plants
                    WHERE plant_name LIKE %s
                       OR description LIKE %s
                    ORDER BY plant_name ASC
                    """,
                    (f"%{search}%", f"%{search}%")
                )
            else:
                cursor.execute(
                    """
                    SELECT plant_id, plant_name, description
                    FROM plants
                    ORDER BY plant_name ASC
                    """
                )

            plant_data = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "manage_plant.html",
        plants=plant_data,
        search=search
    )

@app.route("/admin-plants/upload-dataset/<int:plant_id>", methods=["GET", "POST"])
def upload_dataset(plant_id):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT plant_id, plant_name, description
                FROM plants
                WHERE plant_id = %s
                """,
                (plant_id,)
            )

            plant = cursor.fetchone()

    finally:
        connection.close()

    if not plant:
        flash("Plant not found.", "error")
        return redirect(url_for("admin_plants"))

    if request.method == "POST":

        dataset = request.files.get("dataset")

        if not dataset or dataset.filename == "":
            flash("Please select a dataset ZIP file.", "error")
            return redirect(
                url_for("upload_dataset", plant_id=plant_id)
            )

        if not dataset.filename.lower().endswith(".zip"):
            flash("Please upload a ZIP file.", "error")
            return redirect(
                url_for("upload_dataset", plant_id=plant_id)
            )

        # Create dataset folder
        plant_folder = plant["plant_name"].replace(" ", "_")

        dataset_folder = os.path.join(
            "datasets",
            plant_folder
        )

        os.makedirs(dataset_folder, exist_ok=True)

        # Save ZIP
        zip_path = os.path.join(
            dataset_folder,
            "dataset.zip"
        )

        dataset.save(zip_path)

        # Extract ZIP
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(dataset_folder)

        except zipfile.BadZipFile:
            if os.path.exists(zip_path):
                os.remove(zip_path)

            flash("Invalid ZIP dataset.", "error")

            return redirect(
                url_for("upload_dataset", plant_id=plant_id)
            )

            flash(
                f"{plant['plant_name']} dataset uploaded successfully.",
                "success"
            )

            return redirect(
                url_for(
                    "dataset_details",
                    plant_id=plant_id
                )
            )

    return render_template(
        "upload_dataset.html",
        plant=plant
    )

@app.route("/admin-plants/dataset/<int:plant_id>")
def dataset_details(plant_id):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT plant_id, plant_name, description
                FROM plants
                WHERE plant_id = %s
                """,
                (plant_id,)
            )

            plant = cursor.fetchone()

    finally:
        connection.close()

    if not plant:
        flash("Plant not found.", "error")
        return redirect(url_for("admin_plants"))

    # Main dataset folder
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    dataset_folder = os.path.join(
        BASE_DIR,
        "PlantVillageSplit"
    )

    # Image extensions
    image_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    )

    # Classes for each plant
    plant_classes = {

        "Pepper Bell": [
            "Pepper__bell___Bacterial_spot",
            "Pepper__bell___healthy"
        ],

        "Potato": [
            "Potato___Early_blight",
            "Potato___Late_blight",
            "Potato___healthy"
        ],

        "Tomato": [
            "Tomato_Early_blight",
            "Tomato_Late_blight",
            "Tomato_Septoria_leaf_spot",
            "Tomato_healthy"
        ]
    }

    classes = plant_classes.get(
        plant["plant_name"],
        []
    )

    total_images = 0
    train_images = 0
    val_images = 0
    test_images = 0

    # Count images for selected plant
    for split in ["train", "val", "test"]:

        split_folder = os.path.join(
            dataset_folder,
            split
        )

        if not os.path.isdir(split_folder):
            continue

        for class_name in classes:

            class_folder = os.path.join(
                split_folder,
                class_name
            )

            if not os.path.isdir(class_folder):
                continue

            image_count = 0

            for filename in os.listdir(class_folder):

                file_path = os.path.join(
                    class_folder,
                    filename
                )

                if (
                    os.path.isfile(file_path)
                    and filename.lower().endswith(image_extensions)
                ):
                    image_count += 1

            # Add to total
            total_images += image_count

            # Add to correct split
            if split == "train":
                train_images += image_count

            elif split == "val":
                val_images += image_count

            elif split == "test":
                test_images += image_count

    return render_template(
        "dataset_details.html",
        plant=plant,
        total_images=total_images,
        train_images=train_images,
        val_images=val_images,
        test_images=test_images,
        classes=classes
    )

@app.route("/admin-plants/train/<int:plant_id>", methods=["GET", "POST"])
def train_model(plant_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT plant_id, plant_name
                FROM plants
                WHERE plant_id = %s
            """, (plant_id,))

            plant = cursor.fetchone()

    finally:
        connection.close()

    if not plant:
        flash("Plant not found.", "error")
        return redirect(url_for("admin_plants"))

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

    dataset_folder = os.path.join(
        BASE_DIR,
        "PlantVillageSplit"
    )

    train_path = os.path.join(
        dataset_folder,
        "train"
    )

    val_path = os.path.join(
        dataset_folder,
        "val"
    )

    test_path = os.path.join(
        dataset_folder,
        "test"
    )

    image_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    )

    train_images = 0
    val_images = 0
    test_images = 0

    if os.path.isdir(train_path):
        for root, dirs, files in os.walk(train_path):
            for filename in files:
                if filename.lower().endswith(image_extensions):
                    train_images += 1

    if os.path.isdir(val_path):
        for root, dirs, files in os.walk(val_path):
            for filename in files:
                if filename.lower().endswith(image_extensions):
                    val_images += 1

    if os.path.isdir(test_path):
        for root, dirs, files in os.walk(test_path):
            for filename in files:
                if filename.lower().endswith(image_extensions):
                    test_images += 1

    total_images = (
        train_images +
        val_images +
        test_images
    )

    # =========================
    # GET - OPEN TRAIN PAGE
    # =========================

    if request.method == "GET":

        current_status = training_status.get(
            plant_id,
            "idle"
        )

        current_result = ai_training_data.get(
            plant_id
        )

        return render_template(
            "train_model.html",
            plant=plant,
            total_images=total_images,
            train_images=train_images,
            val_images=val_images,
            test_images=test_images,
            training_status=current_status,
            training_result=current_result
        )

    # =========================
    # POST - START TRAINING
    # =========================

    if not os.path.exists(train_path):
        flash(
            "Training dataset was not found.",
            "error"
        )
        return redirect(
            url_for(
                "dataset_details",
                plant_id=plant_id
            )
        )

    if not os.path.exists(val_path):
        flash(
            "Validation dataset was not found.",
            "error"
        )
        return redirect(
            url_for(
                "dataset_details",
                plant_id=plant_id
            )
        )

    if not os.path.exists(test_path):
        flash(
            "Testing dataset was not found.",
            "error"
        )
        return redirect(
            url_for(
                "dataset_details",
                plant_id=plant_id
            )
        )

    # Prevent duplicate training
    if training_status.get(plant_id) == "running":
        flash(
            "Model training is already in progress.",
            "info"
        )

        return redirect(
            url_for(
                "train_model",
                plant_id=plant_id
            )
        )

    # =========================
    # BACKGROUND TRAINING
    # =========================

    def run_training():

        try:

            training_status[plant_id] = "running"

            print("")
            print("====================================")
            print("AI MODEL TRAINING STARTED")
            print("Plant:", plant["plant_name"])
            print("Epochs: 50")
            print("====================================")
            print("")

            new_model = YOLO(
                "yolov8n-cls.pt"
            )

            new_model.train(
                data=dataset_folder,
                epochs=50,
                imgsz=224,
                batch=32,
                project="training_results",
                name="plant_health_9_classes_model",
                exist_ok=True
            )

            # =========================
            # TRAINING RESULT LOCATION
            # =========================

            result_folder = os.path.join(
                BASE_DIR,
                "runs",
                "classify",
                "training_results",
                "plant_health_9_classes_model"
            )

            results_csv = os.path.join(
                result_folder,
                "results.csv"
            )

            results_image = os.path.join(
                result_folder,
                "results.png"
            )

            trained_model_path = os.path.join(
                result_folder,
                "weights",
                "best.pt"
            )

            if not os.path.exists(
                trained_model_path
            ):

                training_status[plant_id] = "failed"

                print(
                    "ERROR: Trained model file was not found."
                )

                return

            # =========================
            # SAVE TRAINED MODEL
            # =========================

            model_folder = os.path.join(
                BASE_DIR,
                "models",
                "trained"
            )

            os.makedirs(
                model_folder,
                exist_ok=True
            )

            final_model_path = os.path.join(
                model_folder,
                "plant_health_9_classes_best.pt"
            )

            shutil.copy2(
                trained_model_path,
                final_model_path
            )

            # =========================
            # SAVE TRAINING RESULT
            # =========================

            ai_training_data[plant_id] = {

                "plant_id": plant_id,

                "plant_name":
                    plant["plant_name"],

                "total_images":
                    total_images,

                "train_images":
                    train_images,

                "val_images":
                    val_images,

                "test_images":
                    test_images,

                "classes": 9,

                "epochs": 50,

                "image_size":
                    "224 × 224",

                "batch_size": 32,

                "model":
                    "YOLOv8n Classification",

                "results_csv":
                    results_csv,

                "results_image":
                    results_image

            }

            training_status[plant_id] = "completed"

            print("")
            print("====================================")
            print("TRAINING SUCCESSFUL")
            print("====================================")
            print("")

        except Exception as error:

            training_status[plant_id] = "failed"

            print("")
            print("====================================")
            print("TRAINING FAILED")
            print(error)
            print("====================================")
            print("")

        finally:

            training_threads.pop(
                plant_id,
                None
            )

    # =========================
    # START THREAD
    # =========================

    training_thread = threading.Thread(
        target=run_training,
        daemon=True
    )

    training_threads[plant_id] = (
        training_thread
    )

    training_thread.start()

    flash(
        "AI model training has started.",
        "success"
    )

    # IMPORTANT:
    # Stay on Train AI Model page
    # while training runs in background.

    return redirect(
        url_for(
            "train_model",
            plant_id=plant_id
        )
    )

@app.route("/admin-plants/training-result/<int:plant_id>")
def training_result(plant_id):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    result = ai_training_data.get(plant_id)

    if not result:
        flash(
            "Training result is not available.",
            "error"
        )

        return redirect(
            url_for(
                "admin_plants"
            )
        )

    if result["plant_id"] != plant_id:
        flash(
            "Training result is not available.",
            "error"
        )

        return redirect(
            url_for(
                "admin_plants"
            )
        )

    return render_template(
        "training_result.html",
        result=result
    )

@app.route("/training-results/<path:filename>")
def training_results(filename):

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    BASE_DIR = os.path.dirname(
        os.path.abspath(__file__)
    )

    result_folder = os.path.join(
        BASE_DIR,
        "runs",
        "classify",
        "training_results",
        "plant_health_9_classes_model"
    )

    return send_from_directory(
        result_folder,
        filename
    )
    
@app.route("/add-plant", methods=["GET", "POST"])
def add_plant():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        plant_name = request.form["plant_name"]
        description = request.form["description"]

        connection = get_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO plants (plant_name, description)
                    VALUES (%s, %s)
                    """,
                    (plant_name, description)
                )

            connection.commit()

        finally:
            connection.close()

        return redirect(url_for("admin_plants"))

    return render_template("add_plant.html")

@app.route("/admin-plants/edit/<int:plant_id>", methods=["GET", "POST"])
def edit_plant(plant_id):
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:

            # Bila admin tekan Save Changes
            if request.method == "POST":
                description = request.form.get("description", "").strip()

                cursor.execute(
                    """
                    UPDATE plants
                    SET description = %s
                    WHERE plant_id = %s
                    """,
                    (description, plant_id)
                )

                connection.commit()

                flash("Plant updated successfully.", "success")
                return redirect(url_for("admin_plants"))

            # Ambil data plant untuk dipaparkan
            cursor.execute(
                """
                SELECT plant_id, plant_name, description
                FROM plants
                WHERE plant_id = %s
                """,
                (plant_id,)
            )

            plant = cursor.fetchone()

            if not plant:
                flash("Plant not found.", "error")
                return redirect(url_for("admin_plants"))

    finally:
        connection.close()

    return render_template(
        "edit_plant.html",
        plant=plant
    )

@app.route("/admin-plants/add-dataset", methods=["GET", "POST"])
def add_dataset():

    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    if request.method == "POST":

        plant_id = request.form.get("plant_id", "").strip()
        plant_name = request.form.get("plant_name", "").strip()
        description = request.form.get("description", "").strip()
        dataset = request.files.get("dataset")

        connection = get_connection()

        try:

            with connection.cursor() as cursor:

                # ==================================================
                # ADD NEW PLANT
                # ==================================================

                if not plant_id:

                    if not plant_name:
                        flash(
                            "Please enter a plant name.",
                            "error"
                        )
                        return redirect(
                            url_for("add_dataset")
                        )

                    cursor.execute(
                        """
                        SELECT plant_id
                        FROM plants
                        WHERE plant_name = %s
                        """,
                        (plant_name,)
                    )

                    if cursor.fetchone():

                        flash(
                            "This plant already exists. Please select it from the existing plant option.",
                            "error"
                        )

                        return redirect(
                            url_for("add_dataset")
                        )

                    # Add plant into database
                    cursor.execute(
                        """
                        INSERT INTO plants
                        (plant_name, description)
                        VALUES (%s, %s)
                        """,
                        (
                            plant_name,
                            description
                        )
                    )

                    connection.commit()

                    # Dataset is optional for new plant
                    if dataset and dataset.filename:

                        if not dataset.filename.lower().endswith(".zip"):

                            flash(
                                "Please upload a ZIP dataset.",
                                "error"
                            )

                            return redirect(
                                url_for("add_dataset")
                            )

                        dataset_folder = os.path.join(
                            "datasets",
                            plant_name.replace(" ", "_")
                        )

                        os.makedirs(
                            dataset_folder,
                            exist_ok=True
                        )

                        zip_path = os.path.join(
                            dataset_folder,
                            dataset.filename
                        )

                        dataset.save(zip_path)

                    flash(
                        f"{plant_name} was added successfully.",
                        "success"
                    )

                # ==================================================
                # UPDATE EXISTING PLANT
                # ==================================================

                else:

                    cursor.execute(
                        """
                        SELECT plant_id, plant_name
                        FROM plants
                        WHERE plant_id = %s
                        """,
                        (plant_id,)
                    )

                    plant = cursor.fetchone()

                    if not plant:

                        flash(
                            "Plant not found.",
                            "error"
                        )

                        return redirect(
                            url_for("add_dataset")
                        )

                    if not dataset or not dataset.filename:

                        flash(
                            "Please upload a ZIP dataset for the existing plant.",
                            "error"
                        )

                        return redirect(
                            url_for("add_dataset")
                        )

                    if not dataset.filename.lower().endswith(".zip"):

                        flash(
                            "Please upload a ZIP dataset.",
                            "error"
                        )

                        return redirect(
                            url_for("add_dataset")
                        )

                    plant_name = plant["plant_name"]

                    # ==================================================
                    # CLASSES FOR EACH PLANT
                    # ==================================================

                    plant_classes = {

                        "Pepper Bell": [
                            "Pepper__bell___Bacterial_spot",
                            "Pepper__bell___healthy"
                        ],

                        "Potato": [
                            "Potato___Early_blight",
                            "Potato___Late_blight",
                            "Potato___healthy"
                        ],

                        "Tomato": [
                            "Tomato_Early_blight",
                            "Tomato_Late_blight",
                            "Tomato_Septoria_leaf_spot",
                            "Tomato_healthy"
                        ]
                    }

                    allowed_classes = plant_classes.get(
                        plant_name,
                        []
                    )

                    # ==================================================
                    # TEMPORARY ZIP LOCATION
                    # ==================================================

                    temp_folder = os.path.join(
                        "datasets",
                        "temp"
                    )

                    os.makedirs(
                        temp_folder,
                        exist_ok=True
                    )

                    zip_path = os.path.join(
                        temp_folder,
                        dataset.filename
                    )

                    dataset.save(zip_path)

                    # ==================================================
                    # MAIN DATASET
                    # ==================================================

                    BASE_DIR = os.path.dirname(
                        os.path.abspath(__file__)
                    )

                    master_dataset = os.path.join(
                        BASE_DIR,
                        "PlantVillageSplit"
                    )

                    copied_images = 0
                    skipped_images = 0

                    try:

                        with zipfile.ZipFile(
                            zip_path,
                            "r"
                        ) as zip_ref:

                            for member in zip_ref.infolist():

                                if member.is_dir():
                                    continue

                                # Convert Windows path to normal path
                                member_path = member.filename.replace(
                                    "\\",
                                    "/"
                                )

                                parts = [
                                    part
                                    for part in member_path.split("/")
                                    if part
                                ]

                                # Need at least:
                                # train / class / image.jpg
                                if len(parts) < 3:
                                    continue

                                # ==================================================
                                # FIND TRAIN / VAL / TEST
                                # EVEN IF ZIP HAS EXTRA ROOT FOLDER
                                # ==================================================

                                split_index = -1

                                for index, part in enumerate(parts):

                                    if part.lower() in [
                                        "train",
                                        "val",
                                        "test"
                                    ]:

                                        split_index = index
                                        break

                                if split_index == -1:
                                    continue

                                # Make sure class + filename exist
                                if len(parts) <= split_index + 2:
                                    continue

                                split = parts[
                                    split_index
                                ].lower()

                                class_name = parts[
                                    split_index + 1
                                ]

                                filename = parts[-1]

                                # ==================================================
                                # CHECK IMAGE
                                # ==================================================

                                if not filename.lower().endswith(
                                    (
                                        ".jpg",
                                        ".jpeg",
                                        ".png",
                                        ".webp"
                                    )
                                ):
                                    continue

                                # ==================================================
                                # ONLY ALLOW CLASSES FOR SELECTED PLANT
                                # ==================================================

                                if class_name not in allowed_classes:

                                    skipped_images += 1
                                    continue

                                # ==================================================
                                # DESTINATION
                                # ==================================================

                                destination_folder = os.path.join(
                                    master_dataset,
                                    split,
                                    class_name
                                )

                                os.makedirs(
                                    destination_folder,
                                    exist_ok=True
                                )

                                destination_file = os.path.join(
                                    destination_folder,
                                    filename
                                )

                                # ==================================================
                                # AVOID DUPLICATE FILENAMES
                                # ==================================================

                                if os.path.exists(
                                    destination_file
                                ):

                                    base, extension = os.path.splitext(
                                        filename
                                    )

                                    counter = 1

                                    while os.path.exists(
                                        destination_file
                                    ):

                                        new_filename = (
                                            f"{base}_new{counter}"
                                            f"{extension}"
                                        )

                                        destination_file = os.path.join(
                                            destination_folder,
                                            new_filename
                                        )

                                        counter += 1

                                # ==================================================
                                # COPY IMAGE
                                # ==================================================

                                with zip_ref.open(
                                    member
                                ) as source:

                                    with open(
                                        destination_file,
                                        "wb"
                                    ) as target:

                                        shutil.copyfileobj(
                                            source,
                                            target
                                        )

                                copied_images += 1

                        # ==================================================
                        # REMOVE TEMP ZIP
                        # ==================================================

                        if os.path.exists(zip_path):
                            os.remove(zip_path)

                        # ==================================================
                        # RESULT MESSAGE
                        # ==================================================

                        if copied_images > 0:

                            flash(
                                f"Dataset for {plant_name} was merged successfully. "
                                f"{copied_images} image(s) added.",
                                "success"
                            )

                        else:

                            flash(
                                f"No images were added for {plant_name}. "
                                f"Please check the ZIP dataset structure and class names.",
                                "error"
                            )

                    except zipfile.BadZipFile:

                        if os.path.exists(zip_path):
                            os.remove(zip_path)

                        flash(
                            "Invalid ZIP dataset.",
                            "error"
                        )

                        return redirect(
                            url_for("add_dataset")
                        )

        except Exception as error:

            connection.rollback()

            flash(
                f"Failed to process dataset: {error}",
                "error"
            )

        finally:

            connection.close()

        return redirect(
            url_for("admin_plants")
        )

    # ==================================================
    # GET EXISTING PLANTS
    # ==================================================

    connection = get_connection()

    try:

        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT plant_id, plant_name, description
                FROM plants
                ORDER BY plant_name ASC
                """
            )

            plants = cursor.fetchall()

    finally:

        connection.close()

    return render_template(
        "add_dataset.html",
        plants=plants
    )

@app.route("/save-report/<int:classification_id>", methods=["POST"])
def save_report(classification_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:

            cursor.execute(
                """
                SELECT classification_id
                FROM classifications
                WHERE classification_id = %s
                AND user_id = %s
                """,
                (
                    classification_id,
                    session["user_id"]
                )
            )

            classification = cursor.fetchone()

            if not classification:
                flash("Classification not found.", "error")
                return redirect(url_for("detect"))

            cursor.execute(
                """
                SELECT report_id
                FROM reports
                WHERE classification_id = %s
                """,
                (classification_id,)
            )

            existing_report = cursor.fetchone()

            if existing_report:
                flash("This result has already been saved.", "error")
                return redirect(url_for("detect", saved="success"))

            cursor.execute(
                """
                INSERT INTO reports
                (
                    classification_id,
                    report_status
                )
                VALUES (%s, %s)
                """,
                (
                    classification_id,
                    "Saved"
                )
            )

        connection.commit()

    except Exception as error:
        connection.rollback()
        flash(f"Failed to save report: {error}", "error")
        return redirect(url_for("detect"))

    finally:
        connection.close()

    return redirect(url_for("detect", saved="success"))

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
