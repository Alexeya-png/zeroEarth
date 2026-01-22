import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()


def main():
    user = os.getenv("DB_USER") or os.getenv("user")
    password = os.getenv("DB_PASSWORD") or os.getenv("password")
    host = os.getenv("DB_HOST") or os.getenv("host")
    port = int(os.getenv("DB_PORT") or os.getenv("port") or 5432)
    dbname = os.getenv("DB_NAME") or os.getenv("dbname") or "postgres"

    if not all([user, password, host]):
        raise SystemExit("Нет DB_USER/DB_PASSWORD/DB_HOST в .env")

    try:
        conn = psycopg2.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            dbname=dbname,
            sslmode="require",
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("SELECT NOW();")
        print("OK:", cur.fetchone()[0])
        cur.close()
        conn.close()
    except Exception as e:
        print("FAILED:", e)


if __name__ == "__main__":
    main()
