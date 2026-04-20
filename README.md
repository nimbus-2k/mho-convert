# MHSaveConverter
Adds MHO JSON account to Account.sql

This script only works with MHServerEmu 1.0.0.

- Download your TAHITI account data using the *!account download* command in-game.
    - The JSON file location will open after typing the command. Located here in the client directory: UnrealEngine3/Binaries/Win64/Download.

- In the server files - MHServerEmu/Data, back up Account.db.

- Copy Account.db to the folder with MHSaveConverter.exe.

- Add your account to Account.db by:
    - Method A: Drag and drop the JSON file to MHSaveConverter.exe.
    - Method B: Copy the JSON file to the folder with MHSaveConverter.exe. Rename the JSON file to "tahiti.json" (No quotes) then run MHSaveConverter.exe.

- Copy the new Account.db to the server files (MHServerEmu/Data)

## Build
- *pip install pyinstaller*
- *pyinstaller --onefile MHSaveConverter.py*