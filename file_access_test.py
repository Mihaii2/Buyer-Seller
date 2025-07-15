import sys
import msvcrt
import os
from time import sleep

# File to manage
DATA_FILE = "dollars_to_risk.txt"

def lock_file(file):
    """Lock the file using Windows file locking."""
    file.seek(0)
    for _ in range(10):  # Retry up to 10 times
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            sleep(0.1)  # Wait briefly before retrying
    return False

def unlock_file(file):
    """Unlock the file."""
    file.seek(0)
    try:
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass

def read_file():
    """Read and return all rows from the file."""
    try:
        with open(DATA_FILE, 'r') as file:
            if not lock_file(file):
                return "Error: Could not acquire file lock"
            lines = file.readlines()
            unlock_file(file)
            return ''.join(lines)
    except FileNotFoundError:
        return "Error: File not found"
    except Exception as e:
        return f"Error: {str(e)}"

def append_row(data):
    """Append a new row to the file."""
    try:
        with open(DATA_FILE, 'a') as file:
            if not lock_file(file):
                return "Error: Could not acquire file lock"
            file.write(data + '\n')
            unlock_file(file)
            return "Success: Row appended"
    except Exception as e:
        return f"Error: {str(e)}"

def modify_row(row_index, new_data):
    """Modify a specific row in the file."""
    try:
        with open(DATA_FILE, 'r+') as file:
            if not lock_file(file):
                return "Error: Could not acquire file lock"
            lines = file.readlines()
            if row_index < 0 or row_index >= len(lines):
                unlock_file(file)
                return "Error: Invalid row index"
            lines[row_index] = new_data + '\n'
            file.seek(0)
            file.writelines(lines)
            file.truncate()
            unlock_file(file)
            return "Success: Row modified"
    except FileNotFoundError:
        return "Error: File not found"
    except Exception as e:
        return f"Error: {str(e)}"

def delete_row(row_index):
    """Delete a specific row from the file."""
    try:
        with open(DATA_FILE, 'r+') as file:
            if not lock_file(file):
                return "Error: Could not acquire file lock"
            lines = file.readlines()
            if row_index < 0 or row_index >= len(lines):
                unlock_file(file)
                return "Error: Invalid row index"
            lines.pop(row_index)
            file.seek(0)
            file.writelines(lines)
            file.truncate()
            unlock_file(file)
            return "Success: Row deleted"
    except FileNotFoundError:
        return "Error: File not found"
    except Exception as e:
        return f"Error: {str(e)}"

def main():
    if len(sys.argv) < 2:
        print("Usage: python file_gateway.py <operation> [args]")
        print("Operations: read, append <data>, modify <row_index> <data>, delete <row_index>")
        sys.exit(1)

    operation = sys.argv[1].lower()

    if operation == "read":
        print(read_file())
    elif operation == "append":
        if len(sys.argv) != 3:
            print("Usage: python file_gateway.py append <data>")
            sys.exit(1)
        print(append_row(sys.argv[2]))
    elif operation == "modify":
        if len(sys.argv) != 4:
            print("Usage: python file_gateway.py modify <row_index> <data>")
            sys.exit(1)
        try:
            row_index = int(sys.argv[2])
            print(modify_row(row_index, sys.argv[3]))
        except ValueError:
            print("Error: row_index must be an integer")
    elif operation == "delete":
        if len(sys.argv) != 3:
            print("Usage: python file_gateway.py delete <row_index>")
            sys.exit(1)
        try:
            row_index = int(sys.argv[2])
            print(delete_row(row_index))
        except ValueError:
            print("Error: row_index must be an integer")
    else:
        print("Invalid operation. Use: read, append, modify, delete")

if __name__ == "__main__":
    main()