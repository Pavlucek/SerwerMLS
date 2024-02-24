import hashlib
import json
import socket
import threading
from datetime import datetime, timezone
import sched
import time


# Response class
class Response:
    def __init__(self, license_valid, description, expired):
        self.license_valid = license_valid
        self.description = description
        self.expired = expired.isoformat() if expired is not None else None

    def is_license_valid(self):
        return self.license_valid

    def get_description(self):
        return self.description

    def get_expired(self):
        return self.expired

    def get_expiry_time(self):
        try:
            expiry_date = datetime.fromisoformat(self.expired[:-1]) if self.expired else None
            return expiry_date.timestamp() if expiry_date else -1
        except ValueError:
            print("Error parsing the expiry date.")
            return -1

    def is_valid(self):
        expiry_time = self.get_expiry_time()
        return expiry_time > datetime.now(timezone.utc).timestamp()


# Request class
class Request:
    def __init__(self, license_user_name, license_key):
        self.license_user_name = license_user_name
        self.license_key = license_key


# LicenseClientAPI class
class LicenseClientAPI:
    def __init__(self):
        self.license_key = None
        self.license_user_name = None
        self.server_port = None
        self.server_address = None
        self.current_token = None
        self.scheduler = sched.scheduler(time.time, time.sleep)

    def start(self, server_address, server_port):
        self.server_address = server_address
        self.server_port = server_port

    def set_license(self, license_user_name, license_key):
        self.license_user_name = license_user_name
        self.license_key = license_key

    def get_license_token(self):
        if self.current_token and self.current_token.description in ["Server not running", "Connection error"]:
            print(f"Cannot get token: {self.current_token.description}")
            return None

        if not self.current_token or not self.current_token.is_valid():
            self.request_license_token()
        return self.current_token

    def update_token(self, new_token):
        if new_token and new_token.is_license_valid():
            self.current_token = new_token
            print(f"Token updated, valid until: {self.current_token.get_expired()}")
            self.schedule_token_renewal(new_token.get_expiry_time())
        else:
            self.current_token = new_token
            print(f"Token not updated, reason: {self.current_token.get_description()}")

    def schedule_token_renewal(self, expiry_time):
        delay = expiry_time - time.time()
        self.scheduler.enter(delay, 1, self.request_license_token)

    def request_license_token(self):
        threading.Thread(target=self._request_license_token_thread).start()

    def _request_license_token_thread(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.server_address, self.server_port))
                request = Request(self.license_user_name, self.license_key)
                sock.sendall(json.dumps(request.__dict__).encode('utf-8'))
                response = sock.recv(1024).decode('utf-8')
                print(f"Raw response received: {response}")  # Debug print

                if response:
                    response_obj = Response(**json.loads(response))
                    self.update_token(response_obj)
                    if response_obj.license_valid:
                        print(f"Token updated, valid until: {response_obj.expired}")
                    else:
                        print(f"Failed to obtain token: {response_obj.description}")
                else:
                    print("Received an empty response from the server.")
                    self.current_token = Response(False, "Empty response from server", None)
        except ConnectionRefusedError:
            print("Failed to connect to the server. The server might not be running.")
            self.current_token = Response(False, "Server not running", None)
        except json.JSONDecodeError:
            print("Failed to parse the server's response as JSON.")
            self.current_token = Response(False, "Invalid JSON response", None)
        except socket.error as e:
            if e.errno == 10054:
                print("Connection was forcibly closed by the remote host.")
                self.current_token = Response(False, "Connection forcibly closed", None)
            else:
                print(f"Socket error occurred: {e}")
                self.current_token = Response(False, "Connection error", None)
        except Exception as e:
            print(f"An error occurred: {e}")
            self.current_token = Response(False, "Connection error", None)

    def stop(self):
        self.scheduler.cancel()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.server_address, self.server_port))
                leave_request = Request(self.license_user_name, "STOP")
                sock.sendall(json.dumps(leave_request.__dict__).encode('utf-8'))
        except Exception as e:
            print(e)

        self.current_token = None
        self.license_user_name = None
        self.license_key = None
        self.server_address = None
        self.server_port = 0


# Function to generate MD5 key
def generate_key(username):
    md5 = hashlib.md5()
    md5.update(username.encode('utf-8'))
    return md5.hexdigest()


# Main function to interact with the LicenseClientAPI
def main():
    try:
        port = int(input("Please enter the port: "))
        if port <= 0 or port > 65535:
            raise ValueError("The port must be a positive number not greater than 65535!")
    except ValueError as e:
        print(e)
        return

    username = input("Please enter the username (md5 will be generated from it): ")

    client_api = LicenseClientAPI()
    client_api.start("127.0.0.1", port)
    client_api.set_license(username, generate_key(username))

    client_api.get_license_token()

    while True:
        command = input()

        if command == "gettoken":
            token = client_api.get_license_token()
            if token and token.is_license_valid():
                print("Valid: true")
            else:
                print("Valid: false")
        elif command == "stop":
            break

    client_api.stop()
    print("Client stopped")


if __name__ == '__main__':
    main()
