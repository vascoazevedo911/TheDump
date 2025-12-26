import React, { useState, useEffect } from 'react';
import './App.css'; 

// Lê as variáveis de ambiente do React. As vars precisam começar com REACT_APP_
// Inclui um fallback para desenvolvimento local direto ou caso a var não esteja definida.
const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';
const GCS_BUCKET_NAME = process.env.REACT_APP_GCS_BUCKET_NAME || 'your-the-dump-bucket'; // Nome do bucket para construir URLs de visualização

function App() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadMessage, setUploadMessage] = useState('');
  const [uploadedDocuments, setUploadedDocuments] = useState([]);
  const [searchTerm, setSearchTerm] = useState('');
  const [searchResults, setSearchResults] = useState([]);

  // Função para lidar com a mudança de ficheiro no input
  const handleFileChange = (event) => {
    setSelectedFile(event.target.files[0]);
  };

  // Função para lidar com o upload do ficheiro para o backend
  const handleUpload = async () => {
    if (!selectedFile) {
      setUploadMessage('Por favor, selecione um ficheiro primeiro.');
      return;
    }

    const formData = new FormData();
    formData.append('file', selectedFile); // 'file' deve corresponder ao nome do parâmetro no endpoint FastAPI

    try {
      setUploadMessage('A carregar...');
      const response = await fetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        body: formData,
        // O navegador define Content-Type automaticamente para FormData
      });

      const data = await response.json();
      if (response.ok) {
        setUploadMessage(`Upload bem-sucedido! ID do Documento: ${data.document_id}. A processar...`);
        // Adiciona o documento à lista para monitorização
        setUploadedDocuments(prev => [
          ...prev, 
          { 
            document_id: data.document_id, 
            filename: selectedFile.name, 
            status: 'PENDING',
            // O gcs_uri exato será obtido na pesquisa, mas construímos um para o link direto após upload
            gcs_uri: `gs://${GCS_BUCKET_NAME}/${data.document_id}/${selectedFile.name}`
          }
        ]);
      } else {
        setUploadMessage(`Erro no upload: ${data.detail || 'Erro desconhecido'}`);
      }
    } catch (error) {
      setUploadMessage(`Erro de rede: ${error.message}`);
      console.error('Erro de upload:', error);
    } finally {
      setSelectedFile(null); // Limpa o ficheiro selecionado após o upload
    }
  };

  // Função para lidar com a pesquisa de documentos
  const handleSearch = async () => {
    if (!searchTerm.trim()) { // Ignora pesquisa com termo vazio ou só espaços
      setSearchResults([]);
      return;
    }

    try {
      // Endpoint de pesquisa na API
      const response = await fetch(`${API_BASE_URL}/search?q=${encodeURIComponent(searchTerm)}`);
      const data = await response.json();
      if (response.ok) {
        setSearchResults(data);
      } else {
        console.error('Erro na pesquisa:', data.detail);
        setSearchResults([]);
      }
    } catch (error) {
      console.error('Erro de rede na pesquisa:', error);
      setSearchResults([]);
    }
  };

  // Efeito para monitorizar o status dos documentos carregados assincronamente
  useEffect(() => {
    const interval = setInterval(async () => {
      // Só faz polling se houver documentos PENDING ou OCR_IN_PROGRESS/INDEXING_IN_PROGRESS
      if (uploadedDocuments.some(doc => 
        doc.status === 'PENDING' || 
        doc.status === 'OCR_IN_PROGRESS' || 
        doc.status === 'INDEXING_IN_PROGRESS'
      )) {
        const updatedDocs = await Promise.all(uploadedDocuments.map(async (doc) => {
          if (doc.status === 'COMPLETED' || doc.status === 'FAILED') {
            return doc; // Já terminou o processamento, não precisa mais de polling
          }
          
          try {
            const response = await fetch(`${API_BASE_URL}/status/${doc.document_id}`);
            const data = await response.json();
            return { ...doc, status: data.status, error_message: data.error_message, gcs_uri: data.gcs_uri };
          } catch (error) {
            console.error(`Erro ao obter status para ${doc.document_id}:`, error);
            // Em caso de erro ao buscar o status, manter o status atual do doc e tentar novamente depois
            return { ...doc, error_message: `Falha ao obter status: ${error.message}` };
          }
        }));
        setUploadedDocuments(updatedDocs);
      }
    }, 5000); // Fazer polling a cada 5 segundos

    return () => clearInterval(interval); // Limpar o intervalo quando o componente desmonta
  }, [uploadedDocuments]); // Dependência: re-executa o efeito se uploadedDocuments mudar

  // Função auxiliar para construir o URL de visualização do GCS
  const getPublicGcsUrl = (gcsUri) => {
    if (!gcsUri) return null;
    // gcsUri está no formato gs://bucket-name/uuid/filename
    // Precisamos converter para https://storage.googleapis.com/bucket-name/uuid/filename
    return gcsUri.replace('gs://', 'https://storage.googleapis.com/');
  };


  return (
    <div className="App">
      <header className="App-header">
        <h1>The Dump: Repositório de Documentos Inteligente</h1>
      </header>

      <main>
        <section className="upload-section">
          <h2>Carregar Documentos</h2>
          <div className="upload-controls">
            <input type="file" onChange={handleFileChange} />
            <button onClick={handleUpload} disabled={!selectedFile}>Carregar</button>
          </div>
          {uploadMessage && <p className="message">{uploadMessage}</p>}

          <h3>Documentos Carregados (Monitorização)</h3>
          <ul className="document-list">
            {uploadedDocuments.map((doc, index) => (
              <li key={doc.document_id || index} className={`document-item status-${doc.status.toLowerCase()}`}>
                <strong>{doc.filename}</strong> - Status: {doc.status}
                {doc.error_message && <span className="error-message"> (Erro: {doc.error_message})</span>}
                {doc.status === 'COMPLETED' && doc.gcs_uri && 
                  <a href={getPublicGcsUrl(doc.gcs_uri)} target="_blank" rel="noopener noreferrer" className="view-link">
                    Ver Original
                  </a>
                }
              </li>
            ))}
          </ul>
        </section>

        <section className="search-section">
          <h2>Pesquisar Documentos</h2>
          <div className="search-controls">
            <input 
              type="text" 
              placeholder="Pesquisar por palavra-chave..." 
              value={searchTerm} 
              onChange={(e) => setSearchTerm(e.target.value)} 
            />
            <button onClick={handleSearch}>Pesquisar</button>
          </div>

          <h3>Resultados da Pesquisa</h3>
          <ul className="search-results-list">
            {searchResults.length === 0 && searchTerm.trim() !== '' && <p>Nenhum resultado encontrado.</p>}
            {searchResults.map((result, index) => (
              <li key={result.document_id || index} className="search-result-item">
                <h4>{result.filename}</h4>
                <p>Score de Relevância: {result.relevance_score ? result.relevance_score.toFixed(2) : 'N/A'}</p>
                {/* Exibe os trechos de texto com a palavra-chave destacada */}
                {result.highlight && result.highlight.length > 0 ? (
                  result.highlight.map((h, i) => <p key={i} dangerouslySetInnerHTML={{ __html: h }} className="highlight-text"></p>)
                ) : (
                  <p>Conteúdo: {result.status === 'COMPLETED' ? 'Clique para ver o original.' : 'Processamento incompleto ou com erro.'}</p>
                )}
                {result.gcs_uri && (
                  <a href={getPublicGcsUrl(result.gcs_uri)} target="_blank" rel="noopener noreferrer" className="view-link">
                    Ver Original
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  );
}

export default App;