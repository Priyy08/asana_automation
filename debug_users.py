import json
import asana
from backend.services import AsanaManager

def test_users():
    try:
        with open("polling_config.json", "r") as f:
            config = json.load(f)
            pat = config.get("pat")
            project_gid = config.get("project_gid")
            
        # print(f"Loaded Config - PAT: {pat[:5]}... Project: {project_gid}")
        
        manager = AsanaManager(pat, project_gid)
        
        # print("\n--- Testing Workspace GID ---")
        ws_gid = manager.get_workspace_gid()
        print(f"WS GID: {ws_gid}")
        
        if ws_gid:
            print("\n--- Testing User Fetch ---")
            users = manager.fetch_workspace_users(ws_gid)
            print(f"Fetched {len(users)} users.")
            if users:
                 print("First 5 users:", list(users.items())[:5])
        else:
            print("Cannot fetch users without Workspace GID.")
            
    except Exception as e:
        print(f"Test Failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_users()
