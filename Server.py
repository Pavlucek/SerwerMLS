import hashlib
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta


class Response:
    def __init__(self, license_user_name, license_valid, description, expired):
        self.license_user_name = license_user_name
        self.license_valid = license_valid
        self.description = description
        self.expired = expired.isoformat() if expired is not None else None

    def to_dict(self):
        return {
            "license_user_name": self.license_user_name,
            "license_valid": self.license_valid,
            "description": self.description,
            "expired": self.expired
        }


class Request:
    def __init__(self, license_user_name, license_key):
        self.license_user_name = license_user_name
        self.license_key = license_key

    @staticmethod
    def from_dict(data):
        return Request(data['license_user_name'], data['license_key'])


class LicenseInfo:
    def __init__(self, license_user_name="", validation_time=0, expiry_time=None, is_used=False):
        self._license_user_name = license_user_name
        self._validation_time = validation_time
        self._expiry_time = expiry_time
        self._is_used = is_used

    @property
    def license_user_name(self):
        return self._license_user_name

    @license_user_name.setter
    def license_user_name(self, value):
        self._license_user_name = value

    @property
    def validation_time(self):
        return self._validation_time

    @validation_time.setter
    def validation_time(self, value):
        self._validation_time = value

    @property
    def expiry_time(self):
        return self._expiry_time

    @expiry_time.setter
    def expiry_time(self, value):
        self._expiry_time = value

    @property
    def is_used(self):
        return self._is_used

    @is_used.setter
    def is_used(self, value):
        self._is_used = value


def handle_client(client_socket):
    with client_socket:
        try:
            data = client_socket.recv(1024)
            if not data:
                return
            print(f"Received data: {data.decode()}")

            # Prepare a JSON response
            response_message = {
                "license_user_name": "Server",
                "license_valid": False,
                "description": "Connection with server has been closed.",
                "expired": None
            }
            client_socket.sendall(json.dumps(response_message).encode())
        except Exception as e:
            print(f"Error handling client: {e}")


class LicenseServer:
    def __init__(self, port):
        self.port = port
        self.licenses = {}
        self.server_socket = None
        self.load_licenses()
        self.executor = ThreadPoolExecutor(max_workers=10)
        self.running = True

    def load_licenses(self):
        try:
            with open('licenses.json', 'r') as file:
                data = json.load(file)
                for item in data['payload']:
                    license_user_name = item.get("LicenceUserName", "")
                    validation_time = item.get("ValidationTime", 0)

                    license_info = LicenseInfo(license_user_name=license_user_name, validation_time=validation_time)

                    self.licenses[license_user_name] = license_info
        except Exception as e:
            print(f"Failed to load licenses: {e}")
            raise

    def schedule_license_expiry_check(self):
        while True:
            now = datetime.now()
            for license_t in self.licenses.values():
                if license_t.expiry_time and license_t.expiry_time < now and license_t.is_used:
                    license_t.is_used = False
            time.sleep(10)  # Check every 10 seconds

    def run(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.bind(('', self.port))
            self.server_socket.listen()
            self.server_socket.settimeout(1)
            print(f"Server is listening on port {self.port}")

            threading.Thread(target=self.schedule_license_expiry_check, daemon=True).start()

            while self.running:
                try:
                    client_socket, _ = self.server_socket.accept()
                except socket.timeout:
                    continue
                except OSError as e:
                    if self.running:
                        print(f"Unexpected OSError in server accept loop: {e}")
                    break
                else:
                    self.executor.submit(handle_client, client_socket)
        except Exception as e:
            print(f"Server encountered an error: {e}")
        finally:
            if self.server_socket:
                self.server_socket.close()

    def stop_server(self):
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        self.executor.shutdown(wait=True)

    def get_licenses(self):
        return self.licenses


def calculate_expiry_date(validation_time):
    return datetime.now() + timedelta(seconds=validation_time)


class ClientHandler(threading.Thread):
    def __init__(self, client_socket, licenses):
        super().__init__()
        self.client_socket = client_socket
        self.licenses = licenses

    def run(self):
        try:
            with self.client_socket as sock:
                data = sock.recv(1024).decode('utf-8')
                request = json.loads(data)

                print(f"Received a request from: {request['license_user_name']}")

                response = self.handle_request(Request(**request))

                if response:
                    response_data = json.dumps(
                        response.__dict__)
                    sock.sendall(response_data.encode('utf-8'))
        except Exception as e:
            print(f"Exception in ClientHandler: {e}")

    def handle_request(self, request):
        license_info = self.licenses.get(request.license_user_name)

        if not license_info:
            # Construct and send a response indicating the token is not found
            response = Response(request.license_user_name, False, "Token not found", None)
            response_data = json.dumps(response.to_dict())
            self.client_socket.sendall(response_data.encode('utf-8'))
            return  # End the method here since no further action is needed

        generated_key = self.generate_key(request.license_user_name)
        if generated_key != request.license_key:
            # Construct and send a response indicating the key is invalid
            response = Response(request.license_user_name, False, "Invalid key", None)
            response_data = json.dumps(response.to_dict())
            self.client_socket.sendall(response_data.encode('utf-8'))
            return

        # If the license exists and key is correct, proceed with the rest of the validation logic
        if not license_info.is_used or (license_info.expiry_time and datetime.now() > license_info.expiry_time):
            license_info.is_used = True
            license_info.expiry_time = datetime.now() + timedelta(seconds=license_info.validation_time)
            self.licenses[request.license_user_name] = license_info
            response = Response(request.license_user_name, True, "License issued", license_info.expiry_time)
        else:
            # License is already in use and not expired, construct a response with the current expiry time
            response = Response(request.license_user_name, True, "License already in use", license_info.expiry_time)

        # Send the constructed response to the client
        response_data = json.dumps(response.to_dict())
        self.client_socket.sendall(response_data.encode('utf-8'))

    @staticmethod
    def generate_key(username):
        md5 = hashlib.md5()
        md5.update(username.encode('utf-8'))
        return md5.hexdigest()


# Example usage
def start_server(port, licenses):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(('', port))
        server_socket.listen()
        print(f"Server listening on port {port}")

        while True:
            client_socket, _ = server_socket.accept()
            client_handler = ClientHandler(client_socket, licenses)
            client_handler.start()


def main():
    try:
        port = int(input("Please enter the port: "))
        if port <= 0 or port > 65535:
            raise ValueError("The port must be a positive number not greater than 65535!")
    except ValueError as e:
        print(e)
        return

    server = LicenseServer(port)
    server_thread = threading.Thread(target=server.run)
    server_thread.start()

    print("NOTE: Type 'break' to stop the server")
    print("NOTE: or 'print' to show all licenses currently in use")

    while True:
        command = input()

        if command == "break":
            break
        elif command == "print":
            licenses = server.get_licenses()
            for license_name, license_info in licenses.items():
                if license_info.is_used:
                    # Calculate the time left if the license has an expiry time
                    if license_info.expiry_time:
                        time_left = (license_info.expiry_time - datetime.now()).total_seconds()
                        print(f"NUL: {license_name}, Time left: {time_left:.2f} seconds")
                    else:
                        # Handle licenses with no expiry time
                        print(f"NUL: {license_name}, Time left: Unlimited")
                else:
                    print(f"NUL: {license_name} is not currently in use.")
            print()

    server.stop_server()
    server_thread.join()


if __name__ == "__main__":
    main()
