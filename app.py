from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash
from dbconnect import get_connection
from ultralytics import YOLO
import os

app = Flask(__name__)
app.secret_key = "plant-health-secret-key"

app.config["UPLOAD_FOLDER"] = "static/uploads"

model = YOLO("models/best.pt")


@app.route("/", methods=["GET", "POST"])
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

                return redirect(url_for("detect"))

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
    return redirect(url_for("login"))

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
    return redirect(url_for("admin_login"))

@app.route("/admin-reports")
def admin_reports():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
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
                JOIN users u ON c.user_id = u.user_id
                JOIN plants p ON c.plant_id = p.plant_id
                ORDER BY c.classification_date DESC
                """
            )

            report_data = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "admin_reports.html",
        reports=report_data
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
                return redirect(url_for("reports"))

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
        flash("Report saved successfully.", "success")

    except Exception as error:
        connection.rollback()
        flash(f"Failed to save report: {error}", "error")

    finally:
        connection.close()

    return redirect(url_for("reports"))


if __name__ == "__main__":
    app.run(debug=True)
