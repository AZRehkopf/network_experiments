# file_sharing.py

### Imports ###

# Built-ins
import argparse
import ipaddress
import json
import logging
import os
import socket
import sys
import threading
from time import sleep

### Globals ###

LOGGING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

# Socket parameters
SDP = 35000
FSP = 35001
BACKLOG = 5
BUFFER_SIZE = 1024

# File transfer protocol parameters
CMD_FIELD_LEN = 1 
FILE_SIZE_FIELD_LEN  = 8 
CMD = { 
    "ERROR": 0,
    "PUT": 1,
    "GET": 2,
    "RLIST": 3,
    "BYE": 4
}

### File Checks ###

if not os.path.isdir(LOGGING_DIR):
    os.mkdir(LOGGING_DIR)

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
class FileSharingServer:
    def __init__(self, directory):
       self.logger = logging.getLogger("server")
       self.sharing_dir = directory
       
       self.discovery_port = SDP
       self.file_port = FSP
       self.local_ip = "0.0.0.0"

       self.discovery_socket = None
       self.service_socket = None

    def start_serivces(self):
        self.initialize_discovery_socket()
        self.initialize_service_socket()

        working_dir = os.getcwd()
        sharing_path = os.path.join(working_dir, self.sharing_dir)

        print("Below is a list of files available on the server:")
        for item in os.listdir(sharing_path):
            if os.path.isfile(os.path.join(sharing_path, item)):
                print(item)
        
        discovery_thread = threading.Thread(target=self.listen_to_discovery, daemon=True)
        discovery_thread.start()
           
        service_thread = threading.Thread(target=self.listen_to_file_sharing, daemon=True)
        service_thread.start()

        try:
            discovery_thread.join()
        except KeyboardInterrupt:
            self.logger.info("Stopping discovery service")
            self.logger.info("Stopping file sharing service")

    def initialize_discovery_socket(self):
        self.logger.info("Starting discovery service...")

        self.discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_socket.bind((self.local_ip, SDP))

        self.logger.info(f"Discovery service listening on port {SDP}")

    def listen_to_discovery(self):
        self.logger.info(f"Discovery service ready for connections")
        try:
            while True:
                self.discovery_handler(self.discovery_socket.recvfrom(BUFFER_SIZE))
        except KeyboardInterrupt:
            self.logger.info(f"Stopping discovery service")
        finally:
            self.discovery_socket.close()

    def discovery_handler(self, connection):
        message = connection[0]
        address = connection[1]

        self.logger.info(f"Discovery request from {address[0]} on port {address[1]}")

        message = message.decode()
        if message == "SERVICE DISCOVERY":
            self.logger.info(f"Valid request from {address[0]} on port {address[1]} sending response")
            hostname = socket.gethostname()    
            ip_addr = socket.gethostbyname(hostname)
            response = {"message":"Andrew's File Sharing Service"}
            self.discovery_socket.sendto(json.dumps(response).encode(), address)
        else:
            self.logger.error(f"Invalid request from {address[0]} on port {address[1]} no response sent")

    def initialize_service_socket(self):
        self.logger.info("Starting file sharing service...")
        
        self.service_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.service_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.service_socket.bind((self.local_ip, self.file_port))
        self.service_socket.listen(BACKLOG)

        self.logger.info(f"File sharing service listening on port {FSP}")

    def listen_to_file_sharing(self):
        self.logger.info("File sharing service ready for connections")
        
        try:
            while True:
                client = self.service_socket.accept()
                handler_thread = threading.Thread(target=self.file_sharing_handler, args=(client,), daemon=True)
                handler_thread.start()
        except KeyboardInterrupt:
            self.logger.info(f"Stopping file sharing service")
        finally:
            self.service_socket.close()

    def file_sharing_handler(self, client):
        connection, address = client
        
        self.logger.info(f"File service connection established with {address[0]} on port {address[1]}")
        
        while True:
            cmd = int.from_bytes(connection.recv(CMD_FIELD_LEN), byteorder='big')

            if cmd == CMD["GET"]:
                filename_bytes = connection.recv(BUFFER_SIZE)
                filename = filename_bytes.decode()

                working_dir = os.getcwd()
                sharing_path = os.path.join(working_dir, self.sharing_dir)
                files = os.listdir(sharing_path)

                if filename in files and os.path.isfile(os.path.join(sharing_path, filename)):
                    local_file = open(os.path.join(sharing_path, filename), 'rb')
                    file_size_bytes = os.path.getsize(os.path.join(sharing_path, filename))
                    file_size_field = file_size_bytes.to_bytes(FILE_SIZE_FIELD_LEN, byteorder='big')

                    payload = file_size_field

                    try:
                        self.logger.info(f"Sending file {filename} to client which is {file_size_bytes} bytes long")
                        connection.sendall(payload)
                        connection.sendfile(local_file)
                        self.logger.info(f"File transfer complete to {address[0]} on port {address[1]}")
                    except socket.error:
                        self.logger.error("Connection terminated mid-transmission, aborting")
                        connection.close()
                        return
                else:
                    self.logger.error(f"Could not find file '{filename}' on server")

                    payload = CMD["ERROR"].to_bytes(FILE_SIZE_FIELD_LEN, byteorder='big')
                    connection.sendall(payload)
                
            elif cmd == CMD["PUT"]:
                filename = connection.recv(BUFFER_SIZE).decode()
                file_size_bytes = int.from_bytes(connection.recv(FILE_SIZE_FIELD_LEN), byteorder='big')
                
                recvd_bytes_total = bytearray()
                try:
                    self.logger.info(f"Starting file trasfer with {address[0]} on port {address[1]}...")
                    while len(recvd_bytes_total) < file_size_bytes:
                        recvd_bytes_total += connection.recv(BUFFER_SIZE)
                    self.logger.info(f"File transfer complete, received {len(recvd_bytes_total)} bytes total")

                    with open(os.path.join(os.getcwd(), os.path.join(self.sharing_dir, filename)), 'wb') as f:
                        f.write(recvd_bytes_total)
                    self.logger.info(f"File recieved successfully from {address[0]} on port {address[1]}")
                except KeyboardInterrupt:
                    self.logger.error("User interupted transfer mid-stream")
                    exit(1)
                except socket.error:
                    self.logger.error("Server closed socket unexpectedly")
                    self.socket.close()
            
            elif cmd == CMD["RLIST"]:
                self.logger.info(f"Sending file list to {address[0]} on port {address[1]}")
                
                working_dir = os.getcwd()
                sharing_path = os.path.join(working_dir, self.sharing_dir)
                file_list = []

                for item in os.listdir(sharing_path):
                    if os.path.isfile(os.path.join(sharing_path, item)):
                        file_list.append(item)
                payload = ",".join(file_list)
                connection.sendall(payload.encode())
            elif cmd == CMD["BYE"]:
                self.logger.info(f"Closing connection with {address[0]} on port {address[1]}")
                connection.close()
                break


class FileSharingClient():
    def __init__(self, directory):
        self.logger = logging.getLogger("client")
        self.sharing_dir = directory

        self.discovery_port = SDP
        self.file_port = FSP
        self.broadcast_address = "255.255.255.255"
        self.server_address = None

        self.discovery_socket = None
        self.file_socket = None

    def run_file_sharing_client(self):
        self.send_discovery_request()
        self.get_commands()

    def send_discovery_request(self):
        self.logger.info("Broadcasting discovery request")
       
        found = False
        count = 0
        found_servers = []

        self.discovery_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.discovery_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.discovery_socket.settimeout(0.5)

        disocvery_message = "SERVICE DISCOVERY".encode()
        self.discovery_socket.sendto(disocvery_message, (self.broadcast_address, self.discovery_port))
        
        while count < 5:
            try:
                response = self.discovery_socket.recvfrom(BUFFER_SIZE)
                address = response[1]
                response = json.loads(response[0].decode())
                if address[0] not in found_servers:
                    self.logger.info(f"{response['message']} found at {address[0]} on port {address[1]}")
                    found_servers.append(address[0])
                    self.server_address = address[0]
                found = True
            except socket.timeout:
                pass
            count += 1
            sleep(0.5)
        
        if not found:
            self.logger.error(f"No services found")
            exit(0)
    
    def connect_to_service(self):
        self.logger.info("Connecting to file sharing service...")

        self.file_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.file_socket.connect((self.server_address, self.file_port))
            self.connected = True
        except socket.error:
            self.logger.error("Could not connect to server")
            self.connected = False

        self.file_socket.settimeout(2)

        self.logger.info("Connected")

    def get_commands(self):
        print("File Sharing Client v1.0\nPlease enter a command:")
        self.connected = False

        while(True):
            try:
                command = input("> ")
            except KeyboardInterrupt:
                if self.connected:
                    payload = CMD["BYE"].to_bytes(CMD_FIELD_LEN, byteorder='big')
                    self.file_socket.send(payload)
                    self.file_socket.close()
                exit(0)

            args = command.split(" ")
            command = args[0]

            if command == "llist":
                working_dir = os.getcwd()
                sharing_path = os.path.join(working_dir, self.sharing_dir)

                for item in os.listdir(sharing_path):
                    if os.path.isfile(os.path.join(sharing_path, item)):
                        print(item)

            elif command == "connect":
                if self.connected:
                    print("Error: Already connected to a file sharing service")
                    continue
                
                try:
                    self.server_address = args[1]
                    self.file_port = int(args[2])
                except IndexError:
                    print("Error: Not enough arguments")
                    continue
                except ValueError:
                    print("Error: Port value invalid")

                try:
                    ipaddress.IPv4Address(self.server_address)
                except ValueError:
                    print("Error: IP is invalid")
                    continue

                self.connect_to_service()

            elif command == "get":
                filename = None
                if not self.connected:
                    print("Error: Not connected to a file sharing service")
                    continue

                try:
                    filename = args[1]
                except IndexError:
                    print("Error: Not enough arguments")
                    continue

                cmd_field = CMD["GET"].to_bytes(CMD_FIELD_LEN, byteorder='big')
                filename_field = filename.encode()
                
                payload = cmd_field + filename_field
                try:
                    self.file_socket.send(payload)
                except socket.error:
                    self.logger.error("Error during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return 
                except socket.timeout:
                    self.logger.error("Timeout during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return

                response = int.from_bytes(self.file_socket.recv(FILE_SIZE_FIELD_LEN), byteorder='big')
                if response == 0:
                    print("Error: Specified file could not be found on the remote server")
                else:
                    recvd_bytes_total = bytearray()
                    try:
                        self.logger.info("Starting file trasfer...")
                        while len(recvd_bytes_total) < response:
                            recvd_bytes_total += self.file_socket.recv(BUFFER_SIZE)
                        self.logger.info(f"File transfer complete, received {len(recvd_bytes_total)} bytes total")

                        with open(os.path.join(os.getcwd(), os.path.join(self.sharing_dir, filename)), 'wb') as f:
                            f.write(recvd_bytes_total)
                        print("Success: file transfered successfully")
                    except KeyboardInterrupt:
                        self.logger.error("User interupted transfer mid-stream")
                        exit(1)
                    except socket.error:
                        self.logger.error("Server closed socket unexpectedly")
                        self.file_socket.close()
                        self.connected = False
                        self.file_socket = None
                        return 
                    except socket.timeout:
                        self.logger.error("Timeout during socket transmission")
                        self.file_socket.close()
                        self.connected = False
                        self.file_socket = None
                        return

            elif command == "put":
                filename = None
                if not self.connected:
                    print("Error: Not connected to a file sharing service")
                    continue

                try:
                    filename = args[1]
                except IndexError:
                    print("Error: Not enough arguments")
                    continue
                
                working_dir = os.getcwd()
                sharing_path = os.path.join(working_dir, self.sharing_dir)
                files = os.listdir(sharing_path)
                
                if filename in files and os.path.isfile(os.path.join(sharing_path, filename)):
                    cmd_field = CMD["PUT"].to_bytes(CMD_FIELD_LEN, byteorder='big')
                    filename_field = filename.encode()

                    payload = cmd_field + filename_field
                    try:
                        self.file_socket.send(payload)
                    except socket.error:
                        self.logger.error("Error during socket transmission")
                        self.file_socket.close()
                        self.connected = False
                        self.file_socket = None
                        return 
                    except socket.timeout:
                        self.logger.error("Timeout during socket transmission")
                        self.file_socket.close()
                        self.connected = False
                        self.file_socket = None
                        return
            
                    local_file = open(os.path.join(sharing_path, filename), 'rb')
                    file_size_bytes = os.path.getsize(os.path.join(sharing_path, filename))
                    file_size_field = file_size_bytes.to_bytes(FILE_SIZE_FIELD_LEN, byteorder='big')

                    payload = file_size_field

                    try:
                        self.logger.info(f"Sending file {filename} to server which is {file_size_bytes} bytes long")
                        self.file_socket.sendall(payload)
                        self.file_socket.sendfile(local_file)
                        self.logger.info(f"File transfer complete to server")
                    except socket.error:
                        self.logger.error("Connection terminated mid-transmission, aborting")
                        self.file_socket.close()
                        return
                else:
                    print(f"Error: Could not find file '{filename}' in local directory")

            elif command == "rlist":
                if not self.connected:
                    print("Error: Not connected to a file sharing service")
                    continue

                payload = CMD["RLIST"].to_bytes(CMD_FIELD_LEN, byteorder='big')
                try:
                    self.file_socket.send(payload)
                except socket.error:
                    self.logger.error("Error during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return 
                except socket.timeout:
                    self.logger.error("Timeout during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return

                file_string = None 
                try:
                    file_string = self.file_socket.recv(BUFFER_SIZE)
                except socket.error:
                    self.logger.error("Error during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return 
                except socket.timeout:
                    self.logger.error("Timeout during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return
                file_string = file_string.decode()
                
                remote_files = file_string.split(',')
                for f in remote_files: print(f)
            
            elif command == "bye":
                if not self.connected:
                    print("Error: Not connected to a file sharing service")
                    continue

                payload = CMD["BYE"].to_bytes(CMD_FIELD_LEN, byteorder='big')
                try:
                    self.file_socket.send(payload)
                except socket.error:
                    self.logger.error("Error during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return 
                except socket.timeout:
                    self.logger.error("Timeout during socket transmission")
                    self.file_socket.close()
                    self.connected = False
                    self.file_socket = None
                    return
                self.logger.info("Closing file sharing connection client")
                self.file_socket.close()
                self.connected = False
            
            else:
                print("Unrecognized command, see a list of supported commands below")

                print("connect [ip] [port]\t- connect to a file sharing service")
                print("llist \t\t\t- list files in the local directory")
                print("rlist \t\t\t- list files in the remote directory")
                print("get [filename] \t\t- get a file from the remote server")
                print("put [filename] \t\t- put a file on the remote server")
                print("bye \t\t\t- exit the client\n")

### Main ###

def verify_dir(directroy):
    working_dir = os.getcwd()
    
    if directroy in os.listdir(working_dir) and os.path.isdir(os.path.join(working_dir, directroy)):
        return True
    elif os.path.lexists(directroy):
        return True
    
    return False       

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="File sharing application that uses TCP and UDP ports")
    parser.add_argument("-s", "--server", action="store_true", help="Runs the file sharing server")
    parser.add_argument("-c", "--client", action="store_true", help="Runs the file sharing client")
    parser.add_argument("-d", "--directory", type=str, help="Directory for sharing files", required=True)
    args = parser.parse_args()
    
    if args.client and args.server:
        print("Cannot run client and server together")
    elif not verify_dir(args.directory):
        print("Directory invalid")
    elif args.server:
        server = FileSharingServer(args.directory)
        server.start_serivces()
    elif args.client:
        client = FileSharingClient(args.directory)
        client.run_file_sharing_client()
    else:
        print("Must specify client or server use -h for help")