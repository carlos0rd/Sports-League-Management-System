import psycopg2
from config import Config

def fix_referee_sequence():
    conn = psycopg2.connect(Config.DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            CREATE SEQUENCE IF NOT EXISTS referees_referee_id_seq;
        """)

        cur.execute("""
            ALTER TABLE referees
            ALTER COLUMN referee_id SET DEFAULT nextval('referees_referee_id_seq');
        """)

        cur.execute("""
            SELECT COALESCE(MAX(referee_id), 0) + 1
            FROM referees;
        """)
        result = cur.fetchone()
        next_id = result[0] if result else 1

        cur.execute("""
            SELECT setval('referees_referee_id_seq', %s, false);
        """, (next_id,))

        cur.execute("""
            ALTER SEQUENCE referees_referee_id_seq
            OWNED BY referees.referee_id;
        """)

        conn.commit()
        print(f"Referee sequence fixed. Next referee_id: {next_id}")

    except Exception as e:
        conn.rollback()
        print("Error fixing referee sequence:", e)

    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    fix_referee_sequence()