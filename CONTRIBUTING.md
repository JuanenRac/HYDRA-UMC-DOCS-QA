# Contributing to HYDRA-UMC-DOCS-QA 🦾

We welcome contributions to the RAG-based AI assistant of the HYDRA-UMC platform.

## Technology Stack
- **Language**: Python 3.12.
- **Hardware**: Hailo-10 M.2 AI Accelerator (40 TOPS).
- **RAG Framework**: LangChain, ChromaDB / FAISS, sentence-transformers (Quantized).
- **Document Formats**: Markdown, PDF, JSON (Schematics).

## Guidelines
1. **Retrieval Grounding**: Ensure that the retrieval logic prioritizes local project documentation over general LLM knowledge.
2. **Embedding Optimization**: Use quantized embedding models compatible with Hailo-10 for fast vector search.
3. **Data Integrity**: When adding new project documentation, ensure it is properly tagged and indexed in the vector database.
4. **Testing**: Validate that the assistant correctly identifies pinouts and protocol specifics from the latest `HYDRA-UMC` firmware docs.
