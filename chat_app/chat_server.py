# chat_server.py

### Imports ###

# Built-ins
import argparse
import ipaddress
import json
import logging
import os
import socket
import struct
import sys
import threading
from time import sleep

### Globals ###

DISCOVERY_PORT = 35001
CRDP = 35000
BACKLOG = 5
BUFFER_SIZE = 2048
CHAT_SERVICE_DISCOVERY = "CHAT SERVICE DISCOVERY REQUEST"

LOGGING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')

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

class ChatServer():
    def __init__(self):
        self.logger = logging.getLogger("server")
        
        self.directory_port = CRDP
        self.discovery_port = DISCOVERY_PORT
        self.directory_ip = "0.0.0.0"
        
        self.directory_service = None
        self.discovery_service = None

        self.chatroom_directory = {}

    def start_services(self):
        discovery_thread = threading.Thread(target=self.start_discovery_service, daemon=True)
        discovery_thread.start()
           
        directory_thread = threading.Thread(target=self.start_directory_service, daemon=True)
        directory_thread.start()

        try:
            discovery_thread.join()
            directory_thread.join()
        except KeyboardInterrupt:
            self.logger.info("Stopping discovery service")
            self.logger.info("Stopping file sharing service")

    def start_discovery_service(self):
        self.logger.info("Starting discovery service...")

        self.discovery_service = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.discovery_service.bind((self.directory_ip, self.discovery_port))

        self.logger.info(f"Discovery service listening on port {self.discovery_port}")
        self.logger.info(f"Discovery service ready for connections")
        
        try:
            while True:
                self.discovery_handler(self.discovery_service.recvfrom(BUFFER_SIZE))
        except KeyboardInterrupt:
            self.logger.info(f"Stopping discovery service")
        finally:
            self.discovery_service.close()

    def discovery_handler(self, connection):
        message = connection[0]
        address = connection[1]

        self.logger.info(f"Discovery request from {address[0]} on port {address[1]}")

        message = message.decode()
        if message == CHAT_SERVICE_DISCOVERY:
            self.logger.info(f"Valid request from {address[0]} on port {address[1]} sending response...")
            hostname = socket.gethostname()    
            ip_addr = socket.gethostbyname(hostname)
            response = {"address":(ip_addr, self.directory_port)}
            self.discovery_service.sendto(json.dumps(response).encode(), address)
            self.logger.info(f"Response sent")
        else:
            self.logger.error(f"Invalid request from {address[0]} on port {address[1]} no response sent")

    def start_directory_service(self):
        self.logger.info("Starting directory service...")

        self.directory_service = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.directory_service.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.directory_service.bind((self.directory_ip, self.directory_port))
        self.directory_service.listen(BACKLOG)

        self.logger.info(f"Chat directory service listening on port {self.directory_port}")
        self.logger.info("Chat directory service ready for connections")
        
        try:
            while True:
                client = self.directory_service.accept()
                handler_thread = threading.Thread(target=self.directory_client_handler, args=(client,), daemon=True)
                handler_thread.start()
        except KeyboardInterrupt:
            self.logger.info(f"Chat directory service stopped")
        finally:
            self.directory_service.close()

    def directory_client_handler(self, client):
        connection, address = client
        self.logger.info(f"Incoming chat directory connection from {address[0]} on port {address[1]}")
        
        while True:
            client_payload = connection.recv(BUFFER_SIZE)
            client_payload = client_payload.decode()

            try:
                data = json.loads(client_payload)
            except json.decoder.JSONDecodeError:
                self.logger.error("Could not parse client request, disconnecting...")
                self.logger.info(f"Closing chat directory connection with {address[0]} on port {address[1]}")
                connection.close()
                break

            cmd = data["cmd"]
            payload = None

            if cmd == "getdir":
                payload = {"status": True, "data": self.chatroom_directory}
                
            elif cmd == "makeroom":
                room_request = data["data"]

                addresses = [self.chatroom_directory[chatroom] for chatroom in self.chatroom_directory.keys()]

                if room_request["address"] in addresses:
                    payload = {"status": False, "data": "Error: Requested address already in use"}
                elif room_request["name"] in self.chatroom_directory.keys():
                    payload = {"status": False, "data": "Error: Requested name already in use"}
                else:
                    self.chatroom_directory[room_request["name"]] = room_request["address"]
                    payload = {"status": True, "data": f"Success: Chat room {room_request['name']} on {room_request['address'][0]} port {room_request['address'][1]} created"}

            elif cmd == "deleteroom":
                delete_request = data["data"]

                if delete_request["name"] in self.chatroom_directory.keys():
                    del self.chatroom_directory[delete_request["name"]]
                    payload = {"status": True, "data": f"Success: Chat room {delete_request['name']} deleted"}
                else:
                    payload = {"status": False, "data": f"Error: No such room '{delete_request['name']}'"}
            
            elif cmd == "bye":
                self.logger.info(f"Closing chat directory connection with {address[0]} on port {address[1]}")
                connection.close()
                break
            
            else:
                payload = {"status": False, "data": "Error: unrecognized command"}

            if not self.send_data(connection, payload): return

    def send_data(self, connection, data):
        data = json.dumps(data).encode()
        
        try:
            connection.sendall(data)
            return True
        except socket.error:
            self.logger.error("Unexpected socket error during tranmission")
            connection.close()
            return False
        

class ChatClient():
    def __init__(self):
        self.logger = logging.getLogger("client")
        
        self.discovery_port = DISCOVERY_PORT
        self.directory_port = CRDP
        self.broadcast_address = "255.255.255.255"
        
        self.server_address = None

        self.discovery_service = None
        self.directory_service = None
        self.chat_service = None

        self.connected_to_dir = False
        self.name_set = False
        self.chat_name = None
        self.directory_downloaded = False
        self.directory = None

    def start_client(self):
        self.find_server()
        self.get_commands()

    def find_server(self):
        self.logger.info("Broadcasting discovery request")
        server_found = False

        self.discovery_service = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.discovery_service.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.discovery_service.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.discovery_service.settimeout(0.5)

        disocvery_message = CHAT_SERVICE_DISCOVERY.encode()
        self.discovery_service.sendto(disocvery_message, (self.broadcast_address, self.discovery_port))
        
        for _ in range(5):
            try:
                response = self.discovery_service.recvfrom(BUFFER_SIZE)
                response = json.loads(response[0].decode())
                self.server_address = response["address"]
                self.logger.info(f"Chat discovery server located at {self.server_address[0]} on port {self.server_address[1]}")
                server_found = True
                break
            except socket.timeout:
                pass
            sleep(0.5)
        
        if not server_found:
            self.logger.error(f"No chat discovery servers found, exiting")
            exit(0)

    def connect_to_chat_directory(self):
        self.logger.info("Connecting to chat directory service...")

        self.directory_service = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.directory_service.connect((self.server_address[0], self.server_address[1]))
            self.connected_to_dir = True
        except socket.error:
            self.logger.error("Could not connect to server")
            self.connected_to_dir = False

        self.directory_service.settimeout(2)

        self.logger.info("Connected to directory service")

    def send_dir_cmd(self, cmd, data):
        payload = {"cmd": cmd, "data": data}
        payload = json.dumps(payload).encode()
        
        try:
            self.directory_service.sendall(payload)
        except socket.error:
            self.logger.error("Unexpected socket error during tranmission")
            self.directory_service.close()
            self.connected_to_dir = False

    def get_response(self):
        try:
            server_payload = self.directory_service.recv(BUFFER_SIZE)
            server_payload = server_payload.decode()
            return json.loads(server_payload)
        except json.decoder.JSONDecodeError:
            self.logger.error("Could not parse server response, disconnecting")
            self.directory_service.close()
            self.connected_to_dir = False
            return None
        except socket.timeout:
            self.logger.error("Server did not respond")
            self.directory_service.close()
            self.connected_to_dir = False
            return None

    def get_commands(self):
        print("Chat Client V1.0")
        
        while True:
            try:
                if self.connected_to_dir:
                    arguments = input("DIR > ").split(" ")
                else:
                    arguments = input("> ").split(" ")
            except KeyboardInterrupt:
                self.logger.info("Exiting chat client")
                if self.connected_to_dir:
                    self.send_dir_cmd("bye", None)
                break
            cmd = arguments[0]

            if cmd == "connect":
                if not self.connected_to_dir:
                    self.connect_to_chat_directory()
                else:
                    print("Error: Already connected to chat directory")

            elif cmd == "getdir":
                if self.connected_to_dir:
                    self.send_dir_cmd("getdir", None)
                else:
                    print("Error: Not connected to chat directory")
                    continue

                response  = self.get_response()
                
                if response == None:
                    continue
                else:
                    if len(response["data"].keys()) == 0:
                        print("No chat rooms found")
                    else: 
                        print("\nRoom Name\tAddress\t\t\tPort")
                        self.directory = response["data"]
                        self.directory_downloaded = True
                        for key in response["data"].keys():
                            print(f"{key}\t\t{response['data'][key][0]}\t\t{response['data'][key][1]}")
                        print()
                             
            elif cmd == "makeroom":
                if not self.connected_to_dir:
                    print("Error: Not connected to chat directory")
                    continue

                try:
                    room_name = arguments[1]
                    address = arguments[2]
                    port = arguments[3]
                except IndexError:
                    print("Error: not enough arguments")
                    print("Usage: makeroom [name] [ip] [port]")
                    continue

                try:
                    address_object = ipaddress.IPv4Address(address)
                except ipaddress.AddressValueError:
                    print("Error: Invalid IP address")
                    continue
                if not address_object.is_multicast:
                    print("Error: IP is not a multicast address (239.0.0.0 to 239.255.255.255)")
                    continue

                if not port.isnumeric():
                    print("Error: Invalid port number, port number must be a positive integer")
                    continue
                elif int(port) < 1023:
                    print("Error: Invalid port number, port numbers 0 - 1023 are reserved for privileged services")
                    continue
                elif int(port) > 65535:
                    print("Error: Invalid port number, max port number is 65535")
                    continue

                self.send_dir_cmd("makeroom", {"name": room_name, "address": (address, int(port))})

                response  = self.get_response()
                if response == None:
                    continue
                else:
                    print(response["data"])

            elif cmd == "deleteroom":
                if not self.connected_to_dir:
                    print("Error: Not connected to chat directory")
                    continue

                try:
                    room_name = arguments[1]
                except IndexError:
                    print("Error: not enough arguments")
                    print("Usage: deleteroom [name]")
                    continue

                self.send_dir_cmd("deleteroom", {"name": room_name})

                response  = self.get_response()
                if response == None:
                    continue
                else:
                    print(response["data"])

            elif cmd == "bye":
                if self.connected_to_dir:
                    self.send_dir_cmd("bye", None)
                    self.connected_to_dir = False
                else:
                    print("Error: Not connected to chat directory")

            elif cmd == "name":
                try:
                    self.chat_name = arguments[1]
                    self.name_set = True
                except IndexError:
                    print("Error: Must specify a chat name")
                    print("Usage: name [display name]")
                    continue
            
                self.logger.info(f"Chat name set to {self.chat_name}")

            elif cmd == "chat":
                if not self.name_set:
                    print("Error: must set name before chatting, use command 'name' to set it")
                    continue

                if not self.directory_downloaded:
                    print("Error: no chat room directory downloaded use command 'connect' and 'getdir' to download it")
                    continue

                try:
                    target_room = arguments[1]
                except IndexError:
                    print("Error: Must specify a room to join")
                    print("Usage: chat [room name]")
                    continue
                
                if target_room not in self.directory.keys():
                    print("Error: Chat room not found")
                else:
                    self.chat_mode(self.directory[target_room])

            else:
                print("\nUnkown command, see supported commands below:")
                print("connect\t\t - connect to the chat service directory")
                print("getdir\t\t - get available chat rooms from directory")
                print("makeroom\t - add a new chat room to the directory")
                print("deleteroom\t - remove a chat room from the directory")
                print("bye\t\t - disconnect from the chat service directory")
                print("name\t\t - set chat display name\n")
                print("chat\t\t - enters chat mode")
        
    def chat_mode(self, address):
        self.logger.info("Entering chat mode")
        
        listen_thread = threading.Thread(target=self.listen_to_multicast, args=(address,), daemon=True)
        listen_thread.start()

        send_thread = threading.Thread(target=self.send_to_multicast, args=(address,), daemon=True)
        send_thread.start()

        try:
            listen_thread.join()
            send_thread.join()
        except KeyboardInterrupt:
            self.logger.info("Exiting chat mode")

    def listen_to_multicast(self, address):
        multicast_group = address[0]
        server_address = ('', address[1])

        listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            listen_socket.bind(server_address)
        except OSError:
            listen_socket.bind(('', address[1]+1))

        group = socket.inet_aton(multicast_group)
        mreq = struct.pack('4sL', group, socket.INADDR_ANY)
        listen_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        while True:
            data, sender_address = listen_socket.recvfrom(BUFFER_SIZE)
            print("\n" + data.decode())
            print("CHAT > ", end='')

    def send_to_multicast(self, address):
        send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_socket.settimeout(0.2)
        ttl = struct.pack('b', 1)
        send_socket.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)

        while True:
            chat_message = input("CHAT > ")
            chat_message = self.chat_name + ": " + chat_message
            send_socket.sendto(chat_message.encode(), (address[0], address[1]))

### Main ###

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chatting application that uses multicast IP addresses")
    parser.add_argument("-s", "--server", action="store_true", help="Runs the chat server")
    parser.add_argument("-c", "--client", action="store_true", help="Runs the chat client")
    args = parser.parse_args()

    if args.client and args.server:
        print("Cannot run client and server together")
    elif args.server:
        server = ChatServer()
        server.start_services()
    elif args.client:
        client = ChatClient()
        client.start_client()
    else:
        print("Must specify client or server use -h for help")