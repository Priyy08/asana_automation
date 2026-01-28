from backend.database import init_db, get_session, TaskHistory
from sqlalchemy import func, text

def check():
    init_db()
    session = get_session()
    if not session:
        print("Could not get DB session.")
        return

    try:
        # PROOF: Get current DB name
        # Use text() to safely execute raw SQL
        result = session.execute(text("SELECT current_database()")).scalar()
        print(f"CONNECTED TO DATABASE: '{result}'")
        
        count = session.query(func.count(TaskHistory.gid)).scalar()
        print(f"Total rows in task_history: {count}")
        
        if count > 0:
            first = session.query(TaskHistory).first()
            print(f"Sample: {first.name} | Expected: {first.expected_end} | Actual: {first.actual_end}")
    except Exception as e:
        print(f"Error querying DB: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    check()
