import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, auth, storage

logger = logging.getLogger(__name__)

_firebase_app = None

def init_firebase():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    cred_path = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH", "serviceAccountKey.json")
    cred_json_env = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    storage_bucket = os.environ.get("FIREBASE_STORAGE_BUCKET")

    try:
        if cred_json_env:
            cred_dict = json.loads(cred_json_env)
            cred = credentials.Certificate(cred_dict)
            options = {"storageBucket": storage_bucket} if storage_bucket else {}
            _firebase_app = firebase_admin.initialize_app(cred, options)
            logger.info("Firebase Admin initialized using FIREBASE_CREDENTIALS_JSON.")
        elif os.path.exists(cred_path):
            cred = credentials.Certificate(cred_path)
            options = {"storageBucket": storage_bucket} if storage_bucket else {}
            _firebase_app = firebase_admin.initialize_app(cred, options)
            logger.info(f"Firebase Admin initialized using {cred_path}.")
        else:
            logger.warning(
                f"Firebase credentials file ({cred_path}) not found. "
                "Firebase features will run in mock/placeholder mode until serviceAccountKey.json is provided."
            )
    except Exception as e:
        logger.error(f"Error initializing Firebase Admin SDK: {e}")

    return _firebase_app


def is_firebase_initialized() -> bool:
    return len(firebase_admin._apps) > 0


def verify_firebase_id_token(id_token: str):
    """
    Verify Firebase ID token sent from client-side JS SDK.
    Returns decoded token dictionary containing uid, email, name, picture, etc.
    """
    init_firebase()
    if not is_firebase_initialized():
        raise RuntimeError("Firebase is not initialized. Please provide serviceAccountKey.json.")
    
    decoded_token = auth.verify_id_token(id_token)
    return decoded_token


def upload_file_to_firebase_storage(file_bytes: bytes, filename: str, content_type: str = "image/png") -> str:
    """
    Upload file bytes to Firebase Storage and return the public URL.
    """
    init_firebase()
    if not is_firebase_initialized():
        raise RuntimeError("Firebase is not initialized. Please configure storageBucket in serviceAccountKey.json.")

    bucket = storage.bucket()
    blob = bucket.blob(filename)
    blob.upload_from_string(file_bytes, content_type=content_type)
    blob.make_public()
    return blob.public_url
