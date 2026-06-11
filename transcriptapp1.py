import streamlit as st
import tempfile
from pathlib import Path
from docx import Document
import subprocess
import ollama
import json
from groq import Groq

# ====================== HELPER FUNCTIONS ======================

def transcribe_with_groq(client, audio_path):
    try:
        with open(audio_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-large-v3-turbo", file=f, response_format="verbose_json"
            )
        return transcription.text
    except Exception as e:
        if "413" in str(e):
            st.warning("File too large, splitting...")
            return transcribe_in_chunks(client, audio_path)
        st.error(f"Transcription error: {e}")
        return "Transcription failed."

def transcribe_in_chunks(client, audio_path):
    # ... (same as before)
    chunks_dir = audio_path.parent / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    full = []
    subprocess.run(["ffmpeg", "-i", str(audio_path), "-f", "segment", "-segment_time", "300", "-c", "copy", str(chunks_dir / "chunk_%03d.wav")], check=True, capture_output=True)
    for chunk in sorted(chunks_dir.glob("chunk_*.wav")):
        with open(chunk, "rb") as f:
            trans = client.audio.transcriptions.create(model="whisper-large-v3-turbo", file=f, response_format="verbose_json")
            full.append(trans.text)
    return " ".join(full)

def generate_mom_groq(client, transcript):
    prompt = """You are an expert professional secretary. Create structured Minutes of Meeting from the Danish transcript.

Return **ONLY** valid JSON with this exact structure:
{
  "summary": "Short professional summary in English (3-5 sentences)",
  "decisions": ["decision 1", "decision 2"],
  "tasks": [
    {"owner": "Name or TBD", "task": "Task description", "deadline": "Date or ASAP"}
  ],
  "risks": ["risk 1", "risk 2"],
  "next_meeting": "Date or description"
}

Transcript:
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt + transcript[:14000]}],
            temperature=0.2,
            max_tokens=2000
        )
        content = response.choices[0].message.content.strip()
        
        # Robust JSON extraction
        start = content.find('{')
        end = content.rfind('}') + 1
        if start != -1 and end > start:
            json_str = content[start:end]
            return json.loads(json_str)
        else:
            return {"summary": "Failed to parse JSON from LLM", "decisions": [], "tasks": [], "risks": [], "next_meeting": ""}
    except Exception as e:
        return {"summary": f"LLM Error: {str(e)}", "decisions": [], "tasks": [], "risks": [], "next_meeting": ""}

def create_word_mom(mom_data, transcript, original_name):
    doc = Document()
    doc.add_heading(f"Referat / Minutes of Meeting - {original_name}", 0)
    
    doc.add_heading("1. Summary / Resumé", level=1)
    doc.add_paragraph(mom_data.get("summary", "No summary was generated."))

    doc.add_heading("2. Key Decisions", level=1)
    for item in mom_data.get("decisions", []):
        doc.add_paragraph(item, style="List Bullet")

    doc.add_heading("3. Action Items / Tasks", level=1)
    table = doc.add_table(rows=1, cols=4)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text = "Owner"
    hdr[1].text = "Task"
    hdr[2].text = "Deadline"
    hdr[3].text = "Status"

    for task in mom_data.get("tasks", []):
        row = table.add_row().cells
        row[0].text = task.get("owner", "TBD")
        row[1].text = task.get("task", "")
        row[2].text = task.get("deadline", "Not specified")
        row[3].text = "Open"

    doc.add_heading("4. Identified Risks", level=1)
    for risk in mom_data.get("risks", []):
        doc.add_paragraph(risk, style="List Bullet")

    doc.add_heading("5. Next Steps", level=1)
    doc.add_paragraph(mom_data.get("next_meeting", "Not specified"))

    doc.add_heading("6. Full Transcript", level=1)
    doc.add_paragraph(transcript[:15000] if len(transcript) > 15000 else transcript)

    doc_path = "generated_mom.docx"
    doc.save(doc_path)
    return doc_path

# ====================== MAIN APP ======================

st.set_page_config(page_title="Transcription & MOM Generator", layout="wide")
st.title("🎙️ Fast Transcription + MOM Generator")
st.markdown("Groq Whisper + Groq LLM (Recommended)")

groq_api_key = st.text_input("Groq API Key", type="password", value="")

if not groq_api_key:
    st.warning("Please enter your Groq API Key")
    st.stop()

client = Groq(api_key=groq_api_key)

uploaded_file = st.file_uploader("Upload Audio or Video File", type=["mp3","wav","m4a","mp4","mov","avi","mkv","wmv"])

if uploaded_file and st.button("🚀 Transcribe & Generate MOM", type="primary"):
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / uploaded_file.name
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        audio_path = Path(tmpdir) / "compressed.wav"
        
        with st.spinner("Compressing audio..."):
            try:
                subprocess.run(["ffmpeg", "-i", str(file_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "-b:a", "64k", str(audio_path)], check=True, capture_output=True)
            except:
                audio_path = file_path

        with st.spinner("Transcribing..."):
            transcript = transcribe_with_groq(client, audio_path)

        st.success("Transcription completed")
        st.text_area("Transcript", transcript, height=250)

        with st.spinner("Generating professional MOM..."):
            mom_data = generate_mom_groq(client, transcript)
            doc_path = create_word_mom(mom_data, transcript, uploaded_file.name)

        st.success("✅ MOM Generated!")

        with open(doc_path, "rb") as f:
            st.download_button(
                label="📥 Download MOM as Word Document",
                data=f,
                file_name=f"MOM_{Path(uploaded_file.name).stem}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary"
            )