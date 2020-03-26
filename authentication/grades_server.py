# grades_server.py
# TCP server to provide students with their grades

### Imports ###

# Built-ins
import asyncio
import getpass
import ipaddress
import json
import logging
import os
import ssl 
import sys

# Third party imports
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

### Globals ###

LOGGING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')

PASSWORD_FILE = "passwords.json"
GRADE_FILE = "course_grades.json"
CERTIFICATE_FILE = "server.crt"
KEY_FILE = "server.key"

ENCRYPTION_KEY = b'yMFnjp1c3bXwYOQfHebuKAS6SYgJrdMZCAWbaDDxO0w='

### File Checks ###

if not os.path.isdir(LOGGING_DIR):
    os.mkdir(LOGGING_DIR)

if not os.path.isdir(DATA_DIR):
    os.mkdir(DATA_DIR)

if not os.path.isfile(os.path.join(DATA_DIR, PASSWORD_FILE)):
    raise FileNotFoundError(f"Could not find password file, please make sure it is located in {DATA_DIR} and is not corrupted")

if not os.path.isfile(os.path.join(DATA_DIR, GRADE_FILE)):
    raise FileNotFoundError(f"Could not find grade file, please make sure it is located in {DATA_DIR} and is not corrupted")

if not os.path.isfile(os.path.join(DATA_DIR, CERTIFICATE_FILE)):
    raise FileNotFoundError(f"Could not find server certificate, please make sure it is located in {DATA_DIR} and is not corrupted")

if not os.path.isfile(os.path.join(DATA_DIR, KEY_FILE)):
    raise FileNotFoundError(f"Could not find server key, please make sure it is located in {DATA_DIR} and is not corrupted")

### Logging Configuration ###

logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s - %(name)s - %(message)s",
    datefmt='%d/%m/%Y %H:%M:%S',
    handlers=[
        logging.FileHandler(os.path.join(LOGGING_DIR, 'server.log')),
        logging.StreamHandler(sys.stdout)
    ])

### Classes ###

class GradesServer():
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port
        self.client_ip = None
        self.client_port = None

        self.logger = logging.getLogger('server')
        
        self.data = None
        self.user = None
        self.password = None

    async def handle_request(self, reader, writer):
        self.data = await reader.read(1024)
        addr = writer.get_extra_info('peername')
        self.client_ip = addr[0]
        self.client_port = addr[1]
        payload = {"message": None, "data": None}

        self.logger.info(f"Connection established with {self.client_ip} on port {self.client_port}")
        
        if (self.parse_data()):
            correct_pasword = self.verify_password()
            if correct_pasword:
                self.logger.info(f"Sending ID {self.user}'s grade info")
                payload["message"] = f"Received authenticated request from ID {self.user}"
                payload["data"] = self.get_grades()
            else:
                payload["message"] = "Student ID and password did not match"
        else:
            payload["message"] = "Student ID and password did not match"
        
        writer.write(json.dumps(payload).encode())
        await writer.drain()

        self.logger.info(f"Connection with with {self.client_ip} on port {self.client_port} terminated")
        writer.close()

    def parse_data(self):
        try:
            credentials = json.loads(self.data.decode())
        except json.decoder.JSONDecodeError:
            self.logger.error(f"Could not parse data from {self.client_ip} on port {self.client_port}")
            return False
        
        try:
            if "password" in credentials.keys() and "user" in credentials.keys():
                self.logger.info(f"Credentials received from {self.client_ip} on port {self.client_port}")
                
                encryption = Fernet(ENCRYPTION_KEY)
                self.user = encryption.decrypt(credentials["user"].encode())
                self.user =  self.user.decode()
                self.password = encryption.decrypt(credentials["password"].encode())
                self.password = self.password.decode()
                return True 
            else:
                self.logger.error(f"Invalid credentials format received from {self.client_ip} on port {self.client_port}")
                return False
        except AttributeError:
            self.logger.error(f"Could not parse data from {self.client_ip} on port {self.client_port}")
            return False

    def verify_password(self):
        passwords = None
        write_back = False
        hasher = PasswordHasher()
        
        with open(os.path.join(DATA_DIR, PASSWORD_FILE), 'r', encoding='utf8', errors='ignore') as pw_file:
            try:
                passwords = json.load(pw_file)
            except json.decoder.JSONDecodeError:
                self.logger.error(f"Could not parse password file, make sure it exsits and is not corrupted")
                return False

        if self.user in passwords.keys():
            try:
                hasher.verify(passwords[self.user], self.password)
                write_back = hasher.check_needs_rehash(passwords[self.user])
            except VerifyMismatchError:
                self.logger.warning(f"Password verification failed for ID {self.user} from {self.client_ip} on port {self.client_port}")
                return False
        else:
            self.logger.warning(f"Password verification failed for ID {self.user} from {self.client_ip} on port {self.client_port}")
            return False

        if write_back:
            self.logger.info(f"Rehashing ID {self.user}'s password")
            passwords[self.user] = hasher.hash(self.password)
            with open(os.path.join(DATA_DIR, PASSWORD_FILE), 'w', encoding='utf8', errors='ignore') as pw_file:
	            json.dump(passwords, pw_file, ensure_ascii=False, indent=4)
        
        self.logger.info(f"Password verification successful for ID {self.user} from {self.client_ip} on port {self.client_port}")
        return True

    def get_grades(self):
        grades = None
        
        with open(os.path.join(DATA_DIR, GRADE_FILE), 'r', encoding='utf8', errors='ignore') as grade_file:
            try:
                grades = json.load(grade_file)
            except json.decoder.JSONDecodeError:
                self.logger.error(f"Could not parse grades file, make sure it exsits and is not corrupted")
                return "Internal error: could not parse grades file"
        
        return grades[self.user]

    def start(self):
        try:
            asyncio.run(self.run_server())
        except KeyboardInterrupt:
            self.logger.info(f"Stopping {self.__class__.__name__} on {self.ip} port {self.port}")
    
    async def run_server(self):    
        self.logger.info(f"Starting {self.__class__.__name__} on {self.ip} port {self.port}")
        
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(os.path.join(DATA_DIR, CERTIFICATE_FILE), os.path.join(DATA_DIR, KEY_FILE))
        
        server = await asyncio.start_server(
                self.handle_request, 
                self.ip, 
                self.port,
                ssl=ssl_context)

        async with server:
            await server.serve_forever()


class GradesClient():
    def __init__(self, ip, port):
        self.ip = ip
        self.port = port

        self.user = None
        self.password = None
        self.data = None

        self.logger = logging.getLogger('client')

    async def grades_client(self):
        ssl_context = ssl._create_unverified_context()
        
        message = self.get_credentials()

        reader, writer = await asyncio.open_connection(
            self.ip, 
            self.port,
            ssl=ssl_context)

        writer.write(message.encode())

        self.data = await reader.read(1024)
        self.parse_response()
        writer.close()

    def start_client(self):
        self.logger.info(f"Starting {self.__class__.__name__}, will try to connect to {self.ip} on port {self.port}")
        while True:
            try:
                asyncio.run(self.grades_client())
            except KeyboardInterrupt:
                self.logger.info(f"Closing {self.__class__.__name__}")
                break

    def get_credentials(self):
        self.user = input("Student ID: ")
        self.password = getpass.getpass()
        self.logger.info(f"Credentials for student ID {self.user} received")

        encryption = Fernet(ENCRYPTION_KEY)
        self.user = encryption.encrypt(self.user.encode())
        self.password = encryption.encrypt(self.password.encode())
        self.logger.info(f"Hashed ID: {self.user}")
        self.logger.info(f"Hashed Password: {self.password}")

        payload = {"user": self.user.decode(), "password": self.password.decode()}
        self.logger.info(f"Transmitted hash values to server")
        
        return json.dumps(payload)

    def parse_response(self):
        try:
            self.data = json.loads(self.data.decode())
        except json.decoder.JSONDecodeError:
            self.logger.error(f"Could not parse server response")
            return
        
        if self.data["data"] == None:
            print(self.data["message"])
        else:
            print(f"Last Name:\t {self.data['data']['last']}")
            print(f"First Name:\t {self.data['data']['first']}")

            for subject in self.data['data']['grades'].keys():
                print(f"{subject.capitalize()}:\t {self.data['data']['grades'][subject]}")

### Main ###

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Error: not enough arguments, format: 'grades_server.py -c/-s ip port'")
        sys.exit()
    
    if len(sys.argv) > 4:
        print("Error: too many arguments, format: 'grades_server.py -c/-s ip port'")
        sys.exit()

    if sys.argv[1] != "-c" and sys.argv[1] != "-s":
        print(f"Unkown argument: {sys.argv[1]}, accepted arguments are -s for server or -c for client")
        sys.exit()

    try: 
        ipaddress.ip_address(sys.argv[2])
    except ValueError:
        print(f"Invalid ip address, {sys.argv[2]} is not a valid IPv4 address")
        sys.exit()

    if not sys.argv[3].isnumeric():
        print("Invalid port number, port number must be a positive integer")
        sys.exit()
    elif int(sys.argv[3]) < 1023:
        print("Invalid port number, port numbers 0 - 1023 are reserved for privileged services")
        sys.exit()
    elif int(sys.argv[3]) > 65535:
        print("Invalid port number, max port number is 65535")
        sys.exit()
    
    if sys.argv[1] == "-c":
        client = GradesClient(sys.argv[2], int(sys.argv[3]))
        client.start_client()
    elif sys.argv[1] == "-s":
        server = GradesServer(sys.argv[2], int(sys.argv[3]))
        server.start()