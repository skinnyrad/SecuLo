from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import serial
import serial.tools.list_ports
import re
import logging

app = Flask(__name__)
socketio = SocketIO(app)
serial_port = None
# Configure logging
logging.basicConfig(level=logging.DEBUG)
callsign = "SKINNY"
dst_callsign = "BROADCAST"
key = "SecuLo-Default-Key"
def xor_encrypt(plaintext, key):
    """
    XOR encrypts the plaintext with the given key and returns the ciphertext as a hex string.
    
    Args:
        plaintext (str): The plaintext to be encrypted.
        key (str): The key to use for encryption.
        
    Returns:
        str: The ciphertext as a hex string.
    """
    # Convert the plaintext and key to bytes
    plaintext_bytes = plaintext.encode()
    key_bytes = key.encode()
    
    # Repeat the key to match the length of the plaintext
    key_repeated = (key_bytes * (len(plaintext_bytes) // len(key_bytes) + 1))[:len(plaintext_bytes)]
    
    # XOR the plaintext with the repeated key
    ciphertext_bytes = bytes([a ^ b for a, b in zip(plaintext_bytes, key_repeated)])
    
    # Convert the ciphertext bytes to a hex string
    ciphertext_hex = ciphertext_bytes.hex()
    
    return ciphertext_hex

def xor_encrypt_decrypt(plaintext, key, encrypt=True):
    try:
        # Convert the plaintext/ciphertext and key to bytes
        data_bytes = plaintext.encode() if encrypt else bytes.fromhex(plaintext)
        key_bytes = key.encode()

        # Repeat the key to match the length of the data
        key_repeated = (key_bytes * (len(data_bytes) // len(key_bytes) + 1))[:len(data_bytes)]

        # XOR the data with the repeated key
        result_bytes = bytes([a ^ b for a, b in zip(data_bytes, key_repeated)])

        # If encrypting, convert the result to a hex string
        if encrypt:
            result_hex = result_bytes.hex()
            return result_hex
        # If decrypting, convert the result to a string
        else:
            result_str = result_bytes.decode()
            return result_str
    except ValueError as e:
        logging.error(f"Error during XOR encrypt/decrypt: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_ports', methods=['GET'])
def get_ports():
    ports = serial.tools.list_ports.comports()
    port_list = [port.device for port in ports]
    return jsonify(port_list)

@app.route('/connect', methods=['POST'])
def connect():
    global serial_port
    port = request.json['port']
    baudrate = request.json.get('baudrate', 9600)
    try:
        serial_port = serial.Serial(port, baudrate)
        socketio.start_background_task(target=read_from_port)
        return jsonify({"status": "connected"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})
    

@app.route('/submit_callsigns', methods=['POST'])
def submit_callsigns():
    global callsign, dst_callsign
    callsign = request.json['src_callsign']
    dst_callsign = request.json['dst_callsign']
    if len(callsign) > 20 or len(dst_callsign) > 20:
        callsign = "SKINNY"
        dst_callsign = "BROADCAST"
        return jsonify({"status": "error", "message": "Callsign too long"})
    if len(callsign) < 1:
        callsign = 'SKINNY'
    if len(dst_callsign) < 1:
        dst_callsign = 'BROADCAST'
    if dst_callsign.upper() == "BROADCAST":
        dst_callsign = "BROADCAST"
    logging.debug(f"Submitted callsigns: Source - {callsign}, Destination - {dst_callsign}")
    return jsonify({"status": "success", "src_callsign": callsign, "dst_callsign": dst_callsign})


@app.route('/send', methods=['POST'])
def send():
    global serial_port, callsign, dst_callsign
    initial_msg = f"{callsign} -> {dst_callsign}: {request.json['message']}"
    encrypted_message = xor_encrypt_decrypt(initial_msg, key, encrypt=True)
    if encrypted_message is None:
        return jsonify({"status": "error", "message": "Encryption failed"})

    message = "TX:SECULO:" + str(encrypted_message) + "\n"
    if serial_port and serial_port.is_open:
        serial_port.write(message.encode())
        print(message.encode())
        socketio.emit('serial_data', {'data': "👨🏽‍💻 " + initial_msg})
        return jsonify({"status": "sent"})
    return jsonify({"status": "error", "message": "Serial port not connected"})


def read_from_port():
    global serial_port, callsign
    while True:
        if serial_port and serial_port.is_open:
            data = serial_port.readline().decode('utf-8').strip()
            logging.debug(f"Received data: {data}")

            # Step 1: Check for the 'SECULO' string
            pattern1 = r"SECULO:(.+)"
            match1 = re.search(pattern1, data)

            if match1:
                encrypted_data = match1.group(1)
                decrypted_msg = xor_encrypt_decrypt(encrypted_data, key, encrypt=False)
                if decrypted_msg is None:
                    logging.error("Decryption failed due to invalid hexadecimal input")
                    continue

                # Step 2: Extract the source and destination callsigns from the decrypted message
                pattern2 = r"(.+?)->(.+?):(.+)"
                match2 = re.search(pattern2, decrypted_msg)

                if match2:
                    src_callsign = match2.group(1)
                    dst_callsign = match2.group(2)
                    message_text = match2.group(3)
                    logging.debug(f"Source callsign: {src_callsign}, Destination callsign: {dst_callsign}, Message: {message_text}")

                    # Check if the message is intended for the current user
                    if dst_callsign.upper().strip() == 'BROADCAST' or dst_callsign.upper().strip() == callsign.upper().strip():
                        if callsign.upper().strip() != src_callsign.upper().strip():
                            msg_recv = f"👤 {src_callsign}->{dst_callsign}: {message_text}"
                            socketio.emit('serial_data', {'data': msg_recv})
                    else:
                        logging.debug(f"Message not intended for {callsign}, ignoring.")

if __name__ == '__main__':
    socketio.run(app, debug=True)
