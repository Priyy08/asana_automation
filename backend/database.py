from sqlalchemy import create_engine, Column, String, Date, DateTime, func, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import json
import os
from datetime import datetime

Base = declarative_base()
CONFIG_FILE = "db_config.json"
DB_URL = None

def get_db_url():
    global DB_URL
    if DB_URL: return DB_URL
    
    # Try loading from config in root
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), CONFIG_FILE)
    if not os.path.exists(config_path):
        print(f"DB Config not found at {config_path}")
        return None
        
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            # postgresql://user:password@host:port/dbname
            DB_URL = f"postgresql://{config['user']}:{config['password']}@{config['host']}:{config['port']}/{config['database']}"
            return DB_URL
    except Exception as e:
        print(f"Error loading DB config: {e}")
        return None

class TaskHistory(Base):
    __tablename__ = 'task_history'
    
    gid = Column(String, primary_key=True)
    name = Column(String)
    expected_start = Column(Date)
    expected_end = Column(Date)
    actual_start = Column(Date)
    actual_end = Column(Date)
    last_updated = Column(DateTime, default=datetime.now, onupdate=datetime.now)

SessionLocal = None

def init_db():
    global SessionLocal
    url = get_db_url()
    if not url:
        print("Database URL not configured. Skipping DB initialization.")
        return
        
    engine = create_engine(url)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    print("Database initialization complete.")

def get_session():
    if not SessionLocal: return None
    return SessionLocal()

def save_baseline(tasks_data):
    """
    Saves the initial state of tasks as baseline (Expected Dates).
    tasks_data: List of dicts with 'gid', 'name', 'start_on', 'due_on'
    """
    session = get_session()
    if not session: return
    
    try:
        count = 0
        for t in tasks_data:
            # Check if exists, if so, maybe don't overwrite baseline? 
            # Or overwrite if it's a fresh Sync? 
            # Let's assume Sync = Fresh Baseline.
            existing = session.query(TaskHistory).filter_by(gid=t['gid']).first()
            if existing:
                session.delete(existing)
            
            new_record = TaskHistory(
                gid=t['gid'],
                name=t['name'],
                expected_start=datetime.strptime(t['start_on'], "%Y-%m-%d").date(),
                expected_end=datetime.strptime(t['due_on'], "%Y-%m-%d").date(),
                actual_start=datetime.strptime(t['start_on'], "%Y-%m-%d").date(),
                actual_end=datetime.strptime(t['due_on'], "%Y-%m-%d").date()
            )
            session.add(new_record)
            count += 1
            
        session.commit()
        print(f"DB: Saved baseline for {count} tasks.")
    except Exception as e:
        session.rollback()
        print(f"DB Error save_baseline: {e}")
    finally:
        session.close()

def update_actuals(tasks_data):
    """
    Updates the 'actual' dates in DB based on current Asana state.
    Does NOT change 'expected' dates.
    """
    session = get_session()
    if not session: return
    
    try:
        updated = 0
        for t in tasks_data:
            gid = t.get('gid')
            if not gid: continue
            
            record = session.query(TaskHistory).filter_by(gid=gid).first()
            if record:
                # Update actuals
                current_start = datetime.strptime(t['start_on'], "%Y-%m-%d").date()
                current_end = datetime.strptime(t['due_on'], "%Y-%m-%d").date()
                
                if record.actual_start != current_start or record.actual_end != current_end:
                    record.actual_start = current_start
                    record.actual_end = current_end
                    record.name = t.get('name', record.name) # Update name if changed
                    updated += 1
            else:
                # New task found in Asana that wasn't in baseline?
                # Create it with Expected = Current (Assuming it was added later)
                # Or leave Expected Empty? Let's equality safely.
                # For now, let's just create it to track it.
                new_record = TaskHistory(
                    gid=gid,
                    name=t.get('name', 'Unknown'),
                    expected_start=datetime.strptime(t['start_on'], "%Y-%m-%d").date(),
                    expected_end=datetime.strptime(t['due_on'], "%Y-%m-%d").date(),
                    actual_start=datetime.strptime(t['start_on'], "%Y-%m-%d").date(),
                    actual_end=datetime.strptime(t['due_on'], "%Y-%m-%d").date()
                )
                session.add(new_record)
                
        if updated > 0:
            session.commit()
            print(f"DB: Updated actuals for {updated} tasks.")
        else:
            session.commit() # Commit new inserts if any
            
    except Exception as e:
        session.rollback()
        print(f"DB Error update_actuals: {e}")
    finally:
        session.close()

def get_all_history():
    session = get_session()
    if not session: return {}
    try:
        rows = session.query(TaskHistory).all()
        # Return dict: gid -> {expected_start, expected_end}
        result = {}
        for r in rows:
            result[r.gid] = {
                "expected_start": r.expected_start.strftime("%Y-%m-%d") if r.expected_start else None,
                "expected_end": r.expected_end.strftime("%Y-%m-%d") if r.expected_end else None
            }
        return result
    except Exception as e:
        print(f"Error fetching history: {e}")
        return {}
    finally:
        session.close()
