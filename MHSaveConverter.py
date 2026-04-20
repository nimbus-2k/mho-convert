import sys
import json
import base64
import sqlite3
import os

def safe_b64decode(data):
    if not data:
        return None
    try:
        return base64.b64decode(data)
    except Exception:
        return None

def build_entity_upserts(table_name, entities):
    statements = []
    for entity in entities:
        entity_copy = entity.copy()
        entity_copy["ArchiveData"] = safe_b64decode(entity_copy.get("ArchiveData"))

        columns = ", ".join(entity_copy.keys())
        placeholders = ", ".join("?" for _ in entity_copy)
        update_clause = ", ".join([f"{col}=excluded.{col}" for col in entity_copy if col != "DbGuid"])

        statements.append((
            f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(DbGuid) DO UPDATE SET {update_clause};",
            tuple(entity_copy.values())
        ))
    return statements

def get_table_name(cursor, expected_name):
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=lower(?) LIMIT 1",
        (expected_name,),
    )
    row = cursor.fetchone()
    return row[0] if row else None

def resolve_email_conflict(cursor, account_table, email):
    while True:
        cursor.execute(f"SELECT Id FROM {account_table} WHERE Email = ?", (email,))
        existing = cursor.fetchone()
        if not existing:
            return ("insert", email, None)

        response = input(
            f"Email '{email}' already exists (Id={existing[0]}). "
            "Choose: [o]verwrite / [r]ename: "
        ).strip().lower()

        if response in ("o", "overwrite"):
            return ("overwrite", email, existing[0])

        if response in ("r", "rename"):
            new_email = input("Enter new email: ").strip()
            if not new_email:
                print("Email cannot be empty.")
                continue
            email = new_email
            continue

        print("Please enter 'o' to overwrite or 'r' to rename.")

def json_to_sql_inserts(json_data, db_path="Account.db"):
    sql_inserts = {}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    account_table = get_table_name(cursor, "Account")
    player_table = get_table_name(cursor, "Player")
    avatar_table = get_table_name(cursor, "Avatar")
    teamup_table = get_table_name(cursor, "TeamUp")
    item_table = get_table_name(cursor, "Item")
    controlled_entity_table = get_table_name(cursor, "ControlledEntity")
    if not account_table or not player_table:
        conn.close()
        raise ValueError(
            f"Database '{db_path}' is missing required tables. "
            f"Found Account={bool(account_table)}, Player={bool(player_table)}."
        )

    user_data = {
        "Id": json_data.get("Id"),
        "Email": json_data.get("Email"),
        "PlayerName": json_data.get("PlayerName"),
        "PasswordHash": safe_b64decode(json_data.get("PasswordHash")),
        "Salt": safe_b64decode(json_data.get("Salt")),
        "UserLevel": json_data.get("UserLevel", 0),
        "Flags": json_data.get("Flags", 0),
    }

    action, resolved_email, _ = resolve_email_conflict(cursor, account_table, user_data["Email"])
    user_data["Email"] = resolved_email

    if action == "overwrite":
        print(f"Overwriting account '{user_data['Email']}'")
        sql_inserts["Account"] = [(
            f"""UPDATE {account_table} SET
               PlayerName=?, PasswordHash=?, Salt=?, UserLevel=?, Flags=?
               WHERE Email=?""",
            (user_data["PlayerName"], user_data["PasswordHash"], user_data["Salt"], user_data["UserLevel"], user_data["Flags"], user_data["Email"])
        )]
    else:
        if action == "insert" and resolved_email != json_data.get("Email"):
            print(f"Inserting account with renamed email '{resolved_email}'")
        sql_inserts["Account"] = [(
            f"""INSERT INTO {account_table} (Id, Email, PlayerName, PasswordHash, Salt, UserLevel, Flags)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            tuple(user_data.values())
        )]

    player = json_data.get("Player", {})
    player_copy = player.copy()
    player_copy["ArchiveData"] = safe_b64decode(player_copy.get("ArchiveData"))

    columns = ", ".join(player_copy.keys())
    placeholders = ", ".join("?" for _ in player_copy)
    update_clause = ", ".join([f"{col}=excluded.{col}" for col in player_copy if col != "DbGuid"])

    sql_inserts["Player"] = [(
        f"INSERT INTO {player_table} ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(DbGuid) DO UPDATE SET {update_clause};",
        tuple(player_copy.values())
    )]

    if avatar_table:
        sql_inserts["Avatar"] = build_entity_upserts(avatar_table, json_data.get("Avatars", []))
    else:
        print("Warning: Avatar table not found; skipping Avatars import.")

    if teamup_table:
        sql_inserts["TeamUp"] = build_entity_upserts(teamup_table, json_data.get("TeamUps", []))
    else:
        print("Warning: TeamUp table not found; skipping TeamUps import.")

    if item_table:
        sql_inserts["Item"] = build_entity_upserts(item_table, json_data.get("Items", []))
    else:
        print("Warning: Item table not found; skipping Items import.")

    if controlled_entity_table:
        sql_inserts["ControlledEntity"] = build_entity_upserts(controlled_entity_table, json_data.get("ControlledEntities", []))
    else:
        print("Warning: ControlledEntity table not found; skipping ControlledEntities import.")

    conn.close()
    return sql_inserts

def load_json_from_file(file_path):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found at {file_path}")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format. {e}")
    return None

def insert_into_database(sql_statements, db_path="Account.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for table, statements in sql_statements.items():
        for query, values in statements:
            cursor.execute(query, values)
    conn.commit()
    conn.close()
    print(f"Data inserted into {db_path}")

json_file_path = sys.argv[1] if len(sys.argv) > 1 else "tahiti.json"
db_file_path = sys.argv[2] if len(sys.argv) > 2 else "Account.db"
json_data = load_json_from_file(json_file_path)
if json_data:
    if not os.path.exists(db_file_path):
        print(f"Error: Database file not found at {db_file_path}")
        sys.exit(1)
    try:
        sql_statements = json_to_sql_inserts(json_data, db_file_path)
        insert_into_database(sql_statements, db_file_path)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
