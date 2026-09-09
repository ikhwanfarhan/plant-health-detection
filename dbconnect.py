import pymysql

def get_connection():
    return pymysql.connect(
        host="localhost",
        user="root",
        password="",
        database="plant_health_detection",
        cursorclass=pymysql.cursors.DictCursor
    )