# VISION+ TV - stockage persistant configurable
import os, uuid, hashlib, mimetypes
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    import boto3
except ImportError:
    boto3 = None

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

db = SQLAlchemy()

class StorageFolder(db.Model):
    __tablename__ = "storage_folders"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("storage_folders.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    children = db.relationship("StorageFolder", backref=db.backref("parent", remote_side=[id]))

class StorageFile(db.Model):
    __tablename__ = "storage_files"
    id = db.Column(db.Integer, primary_key=True)
    original_name = db.Column(db.String(255), nullable=False)
    storage_key = db.Column(db.String(1024), nullable=False, unique=True)
    folder_id = db.Column(db.Integer, db.ForeignKey("storage_folders.id"), nullable=True)
    mime_type = db.Column(db.String(255), nullable=True)
    size = db.Column(db.BigInteger, nullable=False, default=0)
    extension = db.Column(db.String(32), nullable=True)
    checksum = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    owner_channel_id = db.Column(db.String(128), nullable=True, index=True)
    folder = db.relationship("StorageFolder", backref="files")

class ShareLink(db.Model):
    __tablename__ = "storage_share_links"
    id = db.Column(db.Integer, primary_key=True)
    file_id = db.Column(db.Integer, db.ForeignKey("storage_files.id"), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    file = db.relationship("StorageFile", backref="shares")

class StorageService:
    def __init__(self, app):
        self.app = app
        self.local_root = os.path.abspath(app.config["STORAGE_FOLDER"])
        os.makedirs(self.local_root, exist_ok=True)
        self.backend = app.config.get("STORAGE_BACKEND", "local").lower()
        self.capacity = int(app.config.get("STORAGE_CAPACITY_GB", 2048)) * 1024**3
        self.bucket = app.config.get("STORAGE_BUCKET")
        self.endpoint = app.config.get("STORAGE_ENDPOINT")
        self.s3 = None
        if self.backend == "s3":
            if boto3 is None:
                raise RuntimeError("boto3 est requis avec STORAGE_BACKEND=s3")
            self.s3 = boto3.client(
                "s3",
                endpoint_url=self.endpoint or None,
                aws_access_key_id=app.config.get("STORAGE_ACCESS_KEY"),
                aws_secret_access_key=app.config.get("STORAGE_SECRET_KEY"),
                region_name=app.config.get("STORAGE_REGION", "us-east-1"),
            )

    def used_bytes(self):
        if self.backend == "local":
            total = 0
            for root, _, files in os.walk(self.local_root):
                for name in files:
                    try: total += os.path.getsize(os.path.join(root, name))
                    except OSError: pass
            return total
        return int(db.session.query(func.coalesce(func.sum(StorageFile.size), 0)).scalar() or 0)

    def _safe_name(self, name):
        name = os.path.basename(name).replace("\x00", "").strip()
        return name or "fichier"

    def _key(self, name, folder_id=None):
        safe = self._safe_name(name)
        return (f"folder-{folder_id}/" if folder_id else "") + f"{uuid.uuid4().hex}_{safe}"

    def upload(self, file_storage, folder_id=None, owner_channel_id=None):
        name = self._safe_name(file_storage.filename)
        key = self._key(name, folder_id)
        mime = file_storage.mimetype or mimetypes.guess_type(name)[0] or "application/octet-stream"
        hasher = hashlib.sha256()
        size = 0

        # Stream vers stockage local ou objet tout en calculant le hash.
        if self.backend == "s3":
            import tempfile
            with tempfile.NamedTemporaryFile() as tmp:
                while True:
                    chunk = file_storage.stream.read(8 * 1024 * 1024)
                    if not chunk: break
                    hasher.update(chunk); size += len(chunk); tmp.write(chunk)
                tmp.flush(); tmp.seek(0)
                if self.used_bytes() + size > self.capacity:
                    raise ValueError("Espace de stockage insuffisant")
                self.s3.upload_fileobj(tmp, self.bucket, key, ExtraArgs={"ContentType": mime})
        else:
            if self.used_bytes() >= self.capacity:
                raise ValueError("Espace de stockage insuffisant")
            dest = os.path.abspath(os.path.join(self.local_root, key))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                with open(dest, "wb") as out:
                    while True:
                        chunk = file_storage.stream.read(8 * 1024 * 1024)
                        if not chunk: break
                        hasher.update(chunk); size += len(chunk); out.write(chunk)
                if self.used_bytes() > self.capacity:
                    os.remove(dest)
                    raise ValueError("Espace de stockage insuffisant")
            except Exception:
                if os.path.exists(dest):
                    try: os.remove(dest)
                    except OSError: pass
                raise

        ext = os.path.splitext(name)[1].lower()[:32] or None
        obj = StorageFile(original_name=name, storage_key=key, folder_id=folder_id, owner_channel_id=owner_channel_id,
                          mime_type=mime, size=size, extension=ext,
                          checksum=hasher.hexdigest())
        db.session.add(obj); db.session.commit()
        return obj

    def exists(self, key):
        if self.backend == "s3":
            try: self.s3.head_object(Bucket=self.bucket, Key=key); return True
            except Exception: return False
        return os.path.isfile(os.path.abspath(os.path.join(self.local_root, key)))

    def delete(self, obj):
        if self.backend == "s3":
            self.s3.delete_object(Bucket=self.bucket, Key=obj.storage_key)
        else:
            path = os.path.abspath(os.path.join(self.local_root, obj.storage_key))
            if os.path.isfile(path): os.remove(path)
        db.session.delete(obj); db.session.commit()

    def rename(self, obj, new_name):
        new_name = self._safe_name(new_name)
        old_key = obj.storage_key
        prefix = old_key.rsplit("/", 1)[0] + "/" if "/" in old_key else ""
        new_key = prefix + f"{uuid.uuid4().hex}_{new_name}"
        if self.backend == "s3":
            self.s3.copy_object(Bucket=self.bucket, CopySource={"Bucket": self.bucket, "Key": old_key}, Key=new_key)
            self.s3.delete_object(Bucket=self.bucket, Key=old_key)
        else:
            old = os.path.abspath(os.path.join(self.local_root, old_key))
            new = os.path.abspath(os.path.join(self.local_root, new_key))
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.replace(old, new)
        obj.original_name, obj.storage_key = new_name, new_key
        obj.extension = os.path.splitext(new_name)[1].lower()[:32] or None
        db.session.commit()
        return obj

    def download(self, obj):
        if self.backend == "s3":
            return self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": obj.storage_key}, ExpiresIn=900)
        return os.path.abspath(os.path.join(self.local_root, obj.storage_key))

    def share_url(self, obj, base_url, expiration_minutes=None):
        token = uuid.uuid4().hex + uuid.uuid4().hex
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = datetime.now(timezone.utc) + timedelta(minutes=expiration_minutes) if expiration_minutes else None
        link = ShareLink(file_id=obj.id, token_hash=token_hash, expires_at=expires)
        db.session.add(link); db.session.commit()
        return f"{base_url.rstrip('/')}/share/{quote(token)}"
