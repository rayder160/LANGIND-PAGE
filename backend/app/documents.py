"""
Procesamiento de documentos para indexación RAG.
Soporta PDF y TXT. Divide en chunks semánticos y genera embeddings.
"""
import io
import re
from sqlalchemy.ext.asyncio import AsyncSession
from app.rag import store_chunk

CHUNK_SIZE = 400    # caracteres por chunk
CHUNK_OVERLAP = 80  # solapamiento entre chunks


def extract_text_from_pdf(content: bytes) -> str:
    """Extrae texto de un PDF."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        return "\n\n".join(pages)
    except Exception as e:
        raise ValueError(f"No se pudo leer el PDF: {e}")


def extract_text_from_txt(content: bytes) -> str:
    """Extrae texto de un archivo TXT."""
    try:
        return content.decode("utf-8", errors="ignore")
    except Exception:
        return content.decode("latin-1", errors="ignore")


def split_into_chunks(text: str) -> list[str]:
    """
    Divide el texto en chunks semánticos.
    Intenta dividir por párrafos primero, luego por tamaño.
    """
    # Limpiar texto
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # Dividir por párrafos
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) <= CHUNK_SIZE:
            current += ("\n\n" if current else "") + para
        else:
            if current:
                chunks.append(current)
                # Overlap: llevar el final del chunk anterior
                overlap_text = current[-CHUNK_OVERLAP:] if len(current) > CHUNK_OVERLAP else current
                current = overlap_text + "\n\n" + para
            else:
                # Párrafo muy largo — dividir por oraciones
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sent in sentences:
                    if len(current) + len(sent) <= CHUNK_SIZE:
                        current += (" " if current else "") + sent
                    else:
                        if current:
                            chunks.append(current)
                        current = sent

    if current:
        chunks.append(current)

    return [c for c in chunks if len(c) > 50]  # filtrar chunks muy cortos


async def process_document(
    area_id: str,
    filename: str,
    content: bytes,
    file_type: str,
    db: AsyncSession
) -> int:
    """Procesa un documento y lo indexa en el RAG del área. Retorna número de chunks."""
    if file_type == "pdf":
        text = extract_text_from_pdf(content)
    elif file_type == "txt":
        text = extract_text_from_txt(content)
    else:
        raise ValueError(f"Tipo de archivo no soportado: {file_type}")

    if not text.strip():
        raise ValueError("El documento está vacío o no se pudo extraer texto")

    chunks = split_into_chunks(text)

    for chunk in chunks:
        labeled = f"[Documento: {filename}]\n{chunk}"
        await store_chunk(area_id, labeled, f"document:{filename}", db)

    return len(chunks)
