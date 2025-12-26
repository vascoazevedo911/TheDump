import os
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException
from google.cloud import storage, vision
from elasticsearch import Elasticsearch
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import UUID
import uuid
import datetime
import asyncio
from enum import Enum as PyEnum # Para usar Enum do Python com SQLAlchemy

# --- Carregar Variáveis de Ambiente ---
# Assume que o ficheiro .env está na mesma pasta que main.py
load_dotenv()

# --- Configuração ---
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
ELASTIC_INDEX = "the_dump_documents"

# Google Cloud Clients (Credenciais carregadas via GOOGLE_APPLICATION_CREDENTIALS env var)
# Certifique-se de que a variável de ambiente GOOGLE_APPLICATION_CREDENTIALS
# aponta para o seu ficheiro JSON de credenciais.
# Ou que o IAM está configurado se estiver em um ambiente Google Cloud.
GCS_CLIENT = storage.Client()
VISION_CLIENT = vision.ImageAnnotatorClient()

# Elasticsearch Client
ES_CLIENT = Elasticsearch([os.getenv("ELASTIC_HOST")])

app = FastAPI(title="The Dump API")

# --- Configuração da Base de Dados PostgreSQL (SQLAlchemy) ---
DATABASE_URL = (
    f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
    f"@{os.getenv('POSTGRES_HOST')}:{os.getenv('POSTGRES_PORT')}/{os.getenv('POSTGRES_DB')}"
)
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Definição dos Status de Processamento
class ProcessingStatus(PyEnum): # Usar PyEnum para o Enum do Python
    PENDING = "PENDING"
    OCR_IN_PROGRESS = "OCR_IN_PROGRESS"
    OCR_COMPLETED = "OCR_COMPLETED"
    INDEXING_IN_PROGRESS = "INDEXING_IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

# --- Modelo de Base de Dados (SQLAlchemy) ---
class Document(Base):
    __tablename__ = "documents"
    document_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String, nullable=False)
    file_type = Column(String)
    file_size = Column(String) # Guardado como string para simplificar
    gcs_uri = Column(String, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.datetime.now)
    status = Column(String, default=ProcessingStatus.PENDING.value) # Armazenar o valor string do Enum
    error_message = Column(String, nullable=True)

# Criar tabelas (isto deve ser executado uma vez para inicializar a BD)
@app.on_event("startup")
async def startup_event():
    Base.metadata.create_all(bind=engine)
    # Verificar e criar o índice Elasticsearch se não existir
    if not ES_CLIENT.indices.exists(index=ELASTIC_INDEX):
        ES_CLIENT.indices.create(index=ELASTIC_INDEX)
        print(f"Índice Elasticsearch '{ELASTIC_INDEX}' criado.")


# --- Funções Assíncronas para Processamento ---

async def process_document_pipeline(document_id: uuid.UUID, gcs_uri: str, filename: str):
    """Orquestra o OCR e a indexação de forma assíncrona."""
    db_session = SessionLocal()
    try:
        doc = db_session.query(Document).filter(Document.document_id == document_id).first()
        if not doc:
            print(f"[{document_id}] - ERRO: Documento não encontrado na DB.")
            return

        doc.status = ProcessingStatus.OCR_IN_PROGRESS.value # Atualiza o status
        db_session.commit()
        print(f"[{document_id}] - Status: OCR em andamento...")

        # 1. Executar OCR (Google Vision AI)
        image = vision.Image()
        image.source.image_uri = gcs_uri
        
        # Define os recursos a usar, podemos especificar para documento para PDF
        features = [vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)]
        response = VISION_CLIENT.annotate_image({ 'image': image, 'features': features })
        
        full_text = response.full_text_annotation.text
        
        doc.status = ProcessingStatus.OCR_COMPLETED.value
        db_session.commit()
        print(f"[{document_id}] - Status: OCR Concluído.")
        
        # 2. Indexação (Elasticsearch)
        doc.status = ProcessingStatus.INDEXING_IN_PROGRESS.value
        db_session.commit()
        print(f"[{document_id}] - Status: Indexação em andamento...")

        es_document = {
            "document_id": str(document_id),
            "filename": filename,
            "gcs_uri": gcs_uri,
            "content": full_text,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else datetime.datetime.now().isoformat()
        }
        
        ES_CLIENT.index(index=ELASTIC_INDEX, id=str(document_id), document=es_document)
        
        # 3. Finalizar
        doc.status = ProcessingStatus.COMPLETED.value
        db_session.commit()
        print(f"[{document_id}] - SUCESSO! Documento indexado.")

    except Exception as e:
        db_session.rollback() # Reverter alterações em caso de erro
        doc.status = ProcessingStatus.FAILED.value
        doc.error_message = str(e)
        db_session.commit()
        print(f"[{document_id}] - ERRO na pipeline: {e}")
    finally:
        db_session.close()


# --- Endpoints da API ---

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    """Recebe o ficheiro, guarda no GCS e inicia a pipeline assíncrona."""
    if not GCS_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="GCS_BUCKET_NAME não configurado.")

    document_id = uuid.uuid4()
    bucket = GCS_CLIENT.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(f"{document_id}/{file.filename}")

    # 1. Upload para GCS
    try:
        file_content = await file.read()
        blob.upload_from_string(file_content, content_type=file.content_type)
        gcs_uri = f"gs://{GCS_BUCKET_NAME}/{blob.name}"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar para GCS: {e}")

    # 2. Inserir Metadados na DB
    db_session = SessionLocal()
    try:
        new_doc = Document(
            document_id=document_id,
            filename=file.filename,
            file_type=file.content_type,
            file_size=str(len(file_content)), # Tamanho do ficheiro
            gcs_uri=gcs_uri,
            status=ProcessingStatus.PENDING.value
        )
        db_session.add(new_doc)
        db_session.commit()
        db_session.refresh(new_doc) # Atualiza o objeto com os dados da DB (ex: uploaded_at)
    except Exception as e:
        db_session.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao guardar metadados: {e}")
    finally:
        db_session.close()

    # 3. Acionar o Processamento Assíncrono
    asyncio.create_task(process_document_pipeline(document_id, gcs_uri, file.filename))
    
    return {"message": "Upload iniciado.", "document_id": str(document_id)}


@app.get("/search")
async def search_documents(q: str):
    """Endpoint para pesquisa por palavra-chave no Elasticsearch."""
    try:
        query = {
            "query": {
                "match": {
                    "content": {
                        "query": q,
                        "fuzziness": "AUTO"
                    }
                }
            },
            "highlight": { # Para destacar as palavras na interface
                "fields": {
                    "content": {}
                }
            }
        }
        
        res = ES_CLIENT.search(index=ELASTIC_INDEX, body=query)
        
        results = []
        for hit in res['hits']['hits']:
            doc_id = hit['_source']['document_id']
            # Obtém o status da nossa DB
            db_session = SessionLocal()
            doc_metadata = db_session.query(Document).filter(Document.document_id == uuid.UUID(doc_id)).first()
            db_session.close()

            results.append({
                "document_id": doc_id,
                "filename": hit['_source']['filename'],
                "gcs_uri": hit['_source']['gcs_uri'],
                "relevance_score": hit['_score'],
                "status": doc_metadata.status if doc_metadata else 'DESCONHECIDO',
                "highlight": hit.get('highlight', {}).get('content', [])
            })
            
        return results

    except Exception as e:
        print(f"Erro na pesquisa: {e}")
        raise HTTPException(status_code=500, detail="Erro ao comunicar com o motor de pesquisa.")

@app.get("/status/{document_id}")
async def get_status(document_id: uuid.UUID): # Usa uuid.UUID para validação
    """Endpoint para monitorizar o estado de processamento de um documento."""
    db_session = SessionLocal()
    try:
        doc = db_session.query(Document).filter(Document.document_id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Documento não encontrado.")
        
        return {
            "document_id": str(doc.document_id),
            "filename": doc.filename,
            "gcs_uri": doc.gcs_uri,
            "status": doc.status,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            "error_message": doc.error_message
        }
    finally:
        db_session.close()