// Bytewise Chat Application JavaScript Frontend Script

document.addEventListener('DOMContentLoaded', () => {
    // Get DOM elements as variables for later use in the script

    // Share link window (modal)
    // Share button, Copy link button, Readonly text input field with the page URL
    // Close share modal button
    const shareModal = document.getElementById('share-modal'); 
    const shareBtn = document.getElementById('share-btn'); 
    const copyLink = document.getElementById('copy-link');
    const closeShare = document.getElementById('close-share'); 
    const shareLink = document.getElementById('share-link'); 
    const setKeyBtn = document.getElementById('set-key-btn');
    const endSessionBtn = document.getElementById('end-session-btn');
    const keyModal = document.getElementById('key-modal');
    const closeKey = document.getElementById('close-key');
    const saveKey = document.getElementById('save-key');
    const endSession2 = document.getElementById('end-session-2');
    const apiKeyInput = document.getElementById('api-key-input');
    const keyError = document.getElementById('key-error');

    // Main chat refresh button
    const refreshBtn = document.getElementById('refresh-btn'); 
    
    // Main chat container - scrollable <div> inside <main> that holds all chat messages
    // Any new messages will be appended here
    // Text input field, Send button, File attach button 
    const chatMessages = document.getElementById('chat-messages');
    const messageInput = document.getElementById('message-input'); 
    const sendBtn = document.getElementById('send-btn'); 
    const attachBtn = document.getElementById('attach-btn'); 
    
    // File upload modal - entire modal <div> overlay that appears when user wants to upload files
    // Close upload modal button, Cancel upload modal button, Confirm upload button 
    const uploadModal = document.getElementById('upload-modal');
    const closeUpload = document.getElementById('close-upload'); 
    const cancelUpload = document.getElementById('cancel-upload'); 
    const confirmUpload = document.getElementById('confirm-upload'); 
    
    // Drag & Drop area, <div>
    const dropArea = document.getElementById('drop-area');
    
    // Hidden file input - actual <input type="file"> element (hidden) that opens file browser when triggered
    const fileInput = document.getElementById('file-input');
    
    // File preview container 
    // Section in upload modal that shows selected files (hidden by default)
    // Files list container - <div> inside file preview that holds individual file items
    // File count container
    // <span> inside file count container that shows the actual number of selected files, one number
    const filePreview = document.getElementById('file-preview');
    const filesList = document.getElementById('files-list');
    const fileCount = document.getElementById('file-count');
    const selectedCount = document.getElementById('selected-count');

    // Array to store selected files
    let selectedFiles = [];

    // For checking if chatbot gave answer already
    // If yes, then enable sending new message
    // If not, user has to wait the answer before sending another message
    let chatbotAnswered = true;

    async function fetchWithSession(url, options = {}) {
        return fetch(url, { credentials: 'include', ...options });
    }

    async function sessionHasKey() {
        try {
            const res = await fetchWithSession('/session/status', { headers: { 'Accept': 'application/json' } });
            if (!res.ok) return false;
            const data = await res.json();
            return !!data.has_api_key;
        } catch (_) {
            return false;
        }
    }

    async function promptForApiKey() {
        if (!keyModal) return false;
        if (keyError) {
            keyError.classList.add('hidden');
            keyError.textContent = '';
        }
        if (apiKeyInput) apiKeyInput.value = '';
        keyModal.classList.remove('hidden');
        keyModal.setAttribute('aria-hidden', 'false');
        setTimeout(() => keyModal.classList.add('show'), 10);
        if (apiKeyInput) apiKeyInput.focus();
        return false;
    }

    async function ensureApiKey() {
        const has = await sessionHasKey();
        if (has) return true;
        promptForApiKey();
        return false;
    }

    function resetSendStateAfterAbort() {
        chatbotAnswered = true;
        const empty = !messageInput.value.trim();
        sendBtn.disabled = empty;
    }

    async function endSession() {
        await fetchWithSession('/session/end', { method: 'POST' }).catch(() => {});
        window.location.reload();
    }

    function closeKeyModal() {
        if (!keyModal) return;
        keyModal.classList.remove('show');
        setTimeout(() => {
            keyModal.classList.add('hidden');
            keyModal.setAttribute('aria-hidden', 'true');
        }, 200);
        if (apiKeyInput) apiKeyInput.value = '';
        if (keyError) {
            keyError.classList.add('hidden');
            keyError.textContent = '';
        }
    }

    async function saveApiKey() {
        if (!apiKeyInput) return;
        const key = (apiKeyInput.value || '').trim();
        if (!key) return;
        const res = await fetchWithSession('/session/api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ api_key: key })
        });
        if (!res.ok) {
            const txt = await res.text().catch(() => 'Failed to set key');
            if (keyError) {
                keyError.textContent = txt;
                keyError.classList.remove('hidden');
            }
            return;
        }
        closeKeyModal();
    }

    if (setKeyBtn) setKeyBtn.addEventListener('click', () => { promptForApiKey(); });
    if (endSessionBtn) endSessionBtn.addEventListener('click', () => { endSession(); });
    if (closeKey) closeKey.addEventListener('click', closeKeyModal);
    if (keyModal) keyModal.addEventListener('click', (e) => { if (e.target === keyModal) closeKeyModal(); });
    if (saveKey) saveKey.addEventListener('click', () => { saveApiKey(); });
    if (endSession2) endSession2.addEventListener('click', () => { endSession(); });
    if (apiKeyInput) apiKeyInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveApiKey();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closeKeyModal();
        }
    });

    // Same session model as Research Assistant: prompt for OpenRouter key if missing
    (async () => {
        const has = await sessionHasKey();
        if (!has) promptForApiKey();
    })();


    // -- Send message on Enter key, but allow Shift+Enter for new lines --
    messageInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault(); // Prevent default Enter behavior (new line)
            sendMessage();
        }
    });

    // Auto-resize textarea as user types
    messageInput.addEventListener('input', () => {
        // Reset height to auto to get the correct scrollHeight
        messageInput.style.height = 'auto';
        // Set height based on content, but respect min and max heights
        const newHeight = Math.min(Math.max(messageInput.scrollHeight, 48), 128); // min 48px, max 128px
        messageInput.style.height = newHeight + 'px';
        
        // Enable/disable send button based on input
        hasUserTyped = (messageInput.value.trim() === '');
        sendBtn.disabled = hasUserTyped;
    });

    const sendMessage = async () => {
        const message = messageInput.value.trim();
        if (message === '') return;
        if (chatbotAnswered === false) return;

        const has = await sessionHasKey();
        if (!has) {
            promptForApiKey();
            addMessage('Set your **OpenRouter API key** with the key icon above, then send again. Keys use the same session cookie as Research Assistant.', 'assistant');
            return;
        }

        chatbotAnswered = false;

        if ((message.toLowerCase() === "/start") || (message.toLowerCase() === "start") ||
            (message.toLowerCase() === "/next") || (message.toLowerCase() === "next")) {
            addMessage(message, 'user');
            messageInput.value = '';
            sendBtn.disabled = true;
            fetchChatbotAnalysis(message);
        } else {
            addMessage(message, 'user');
            messageInput.value = '';
            sendBtn.disabled = true;
            fetchChatbotAnswer(message);
        }
    };
    // Backend API call for handling messages
    async function fetchChatbotAnswer(message) {
        try {
            const ok = await ensureApiKey();
            if (!ok) {
                resetSendStateAfterAbort();
                return;
            }
            const response = await fetchWithSession('/chatbot-answer', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ role: 'user', content: message })
            });

            if (response.status === 401) {
                promptForApiKey();
                addMessage('Your session needs an OpenRouter key again (401). Use the key icon, save, then retry.', 'assistant');
                resetSendStateAfterAbort();
                return;
            }

            if (!response.ok) {
                throw new Error('Something went wrong with the request');
            }

            const botMessage = await response.json();
            addMessage(botMessage.content, 'assistant');
            chatbotAnswered = true;
        } catch (error) {
            console.error('Error fetching chatbot answer:', error);
            addMessage('Sorry, I could not process your request. Please try again later.', 'assistant');
            chatbotAnswered = true;
        }
    }
    // Backend API call for handling /start command
    async function fetchChatbotAnalysis(message) {
        try {
            const ok = await ensureApiKey();
            if (!ok) {
                resetSendStateAfterAbort();
                return;
            }
            const response = await fetchWithSession('/analyze', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ role: 'user', content: message })
            });

            if (response.status === 401) {
                promptForApiKey();
                addMessage('Your session needs an OpenRouter key again (401). Use the key icon, save, then run `/start` or `/next` again.', 'assistant');
                resetSendStateAfterAbort();
                return;
            }

            if (!response.ok) {
                throw new Error('Something went wrong with the request');
            }

            const botMessage = await response.json();
            addMessage(botMessage.content, 'assistant');
            chatbotAnswered = true;
        } catch (error) {
            console.error('Error fetching chatbot analysis:', error);
            addMessage('Sorry, I could not process your request. Please try again later.', 'assistant');
            chatbotAnswered = true;
        }
    }


    // -- Send message on button click -- 
    sendBtn.addEventListener('click', sendMessage);

    // --- Add message to chat ----
    const addMessage = (text, sender) => {
        const messageDiv = document.createElement('div');
        messageDiv.className = sender === 'user' ? 'orbs-msg-row orbs-msg-row--user' : 'orbs-msg-row';

        const iconClass = sender === 'user' ? 'fa-user' : 'fa-robot';
        const avClass = sender === 'user' ? 'orbs-avatar orbs-avatar--user' : 'orbs-avatar orbs-avatar--bot';
        const bubbleClass = sender === 'user' ? 'orbs-bubble orbs-bubble--user' : 'orbs-bubble orbs-bubble--bot';

        if (sender === 'user') {
            const textHTML = text.replace(/\n/g, '<br>');
            messageDiv.innerHTML = `
                <div class="${avClass}">
                    <i class="fas ${iconClass}"></i>
                </div>
                <div class="${bubbleClass}">
                    <p>${textHTML}</p>
                </div>
            `;
        } else {
            messageDiv.innerHTML = `
                <div class="${avClass}">
                    <i class="fas ${iconClass}"></i>
                </div>
                <div class="${bubbleClass}">
                    <div class="orbs-md-content">${marked.parse(text)}</div>
                </div>
            `;
        }

        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    // File upload modal 
    // Show upload modal, i.e. remove the view property hidden and add property show
    attachBtn.addEventListener('click', async () => {
        const has = await sessionHasKey();
        if (!has) {
            promptForApiKey();
            return;
        }
        uploadModal.classList.remove('hidden');
        uploadModal.setAttribute('aria-hidden', 'false');
        setTimeout(() => {
            uploadModal.classList.add('show');
        }, 10);
    });

    // Close upload modal
    // Calls reset file input
    const closeUploadModal = () => {
        uploadModal.classList.remove('show');
        setTimeout(() => {
            uploadModal.classList.add('hidden');
            uploadModal.setAttribute('aria-hidden', 'true');
        }, 300);
        resetFileInput();
    };

    closeUpload.addEventListener('click', closeUploadModal);
    cancelUpload.addEventListener('click', closeUploadModal);

    // File input bridge
    // When user selects files, we handle the fired 'change' event
    // This will trigger the handleFiles function to process the selected files
    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });

    // Handle selected files
    // The File API (built-in) makes it possible to access a FileList 
    // containing File objects representing 
    // the files selected by the user.
    // Input: e.target.files, aka FileList object 
    const handleFiles = (files) => {
        // Add new files to selectedFiles array
        // Loop through the FileList object (array-like obj)
        // Do the check for duplicates
        // Do the check for correct file format
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            
            // Check if this file is already in our selectedFiles array
            // We compare both name and size to detect duplicates
            let isDuplicate = false;
            
            // Look through all previously selected files 
            // in our selectedFiles array
            for (let j = 0; j < selectedFiles.length; j++) {
                const existingFile = selectedFiles[j];
                
                // If we find a file with same name AND same size, it's a duplicate
                if (existingFile.name === file.name && existingFile.size === file.size) {
                    isDuplicate = true;
                    break; // Stop looking, we found a duplicate
                }
            }

            let extension = file.name // e.g., 24000000_Name.ipynb
            extension = extension.split(".") // ["24000000_Name", "ipynb"]
            extension = extension[extension.length - 1] // "ipynb"
            
            // Only add the file if it's NOT a duplicate
            if ((isDuplicate == false) && (extension === "ipynb")) {
                selectedFiles.push(file);
            }
        }
        updateFilePreview();
    };

    // Update file preview display
    const updateFilePreview = () => {
        if (selectedFiles.length === 0) {
            filePreview.style.display = 'none';
            confirmUpload.disabled = true;
            confirmUpload.classList.add('is-disabled');
            return;
        }

        filePreview.style.display = 'block';
        fileCount.style.display = 'block';
        selectedCount.textContent = selectedFiles.length;

        filesList.innerHTML = '';

        selectedFiles.forEach((file, index) => {
            const fileItem = document.createElement('div');
            fileItem.className = 'orbs-file-item';

            const fileIcon = getFileIcon(file.type);

            fileItem.innerHTML = `
                <div class="orbs-file-item-main">
                    <i class="fas ${fileIcon}"></i>
                    <span class="orbs-file-item-name">${file.name}</span>
                    <span class="orbs-file-item-meta">(${formatFileSize(file.size)})</span>
                </div>
                <button type="button" class="orbs-file-item-remove" onclick="removeFileByIndex(${index})" aria-label="Remove file">
                    <i class="fas fa-times"></i>
                </button>
            `;

            filesList.appendChild(fileItem);
        });

        confirmUpload.disabled = false;
        confirmUpload.classList.remove('is-disabled');
    };

    // Get appropriate icon for file type
    const getFileIcon = (fileType) => {
        if (fileType.startsWith('image/')) return 'fa-image';
        if (fileType.startsWith('video/')) return 'fa-video';
        if (fileType.startsWith('audio/')) return 'fa-music';
        if (fileType.includes('pdf')) return 'fa-file-pdf';
        if (fileType.includes('word')) return 'fa-file-word';
        if (fileType.includes('excel') || fileType.includes('spreadsheet')) return 'fa-file-excel';
        if (fileType.includes('powerpoint') || fileType.includes('presentation')) return 'fa-file-powerpoint';
        if (fileType.includes('zip') || fileType.includes('rar') || fileType.includes('7z')) return 'fa-file-archive';
        return 'fa-file';
    };

    // Format file size for display
    const formatFileSize = (bytes) => {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    };

    // Remove file by index 
    // It is global function for preview files remove  
    // We made func global, i.e. attached to the window object and not to DOMContentLoaded scope
    // We needed that since HTML was created dynamically
    // This allows us to call it from the HTML onclick attribute
    window.removeFileByIndex = (index) => {
        selectedFiles.splice(index, 1);
        updateFilePreview();
    };

    function resetFileInput() {
        fileInput.value = '';
        selectedFiles = [];
        updateFilePreview();
        // Since there are no files, selectedFiles.length is zero,
        // and so no files will be shown in the preview
    }

    confirmUpload.addEventListener('click', async () => {
        if (selectedFiles.length === 0) {
            closeUploadModal();
            return;
        }

        const hasKey = await sessionHasKey();
        if (!hasKey) {
            promptForApiKey();
            addMessage('Set your **OpenRouter API key** first, then confirm upload again.', 'assistant');
            return;
        }

        if (selectedFiles.length === 1) {
            addMessage(`Uploading file: ${selectedFiles[0].name}...`, 'user');
        } else {
            const fileNames = selectedFiles.map(f => f.name).join(', ');
            addMessage(`Uploading ${selectedFiles.length} files: ${fileNames}...`, 'user');
        }

        try {
            const uploadResult = await sendFilesToBackend(selectedFiles);
            handleUploadResponse(uploadResult);
        } catch (error) {
            console.error('Upload error:', error);
            if (error.message === 'KEY_REQUIRED' || error.message === 'KEY_401') {
                promptForApiKey();
                addMessage(
                    error.message === 'KEY_401'
                        ? 'Session rejected the key (401). Save a new OpenRouter key, then upload again.'
                        : 'OpenRouter key is required for upload. Save it in the modal, then try again.',
                    'assistant'
                );
                return;
            }
            addMessage('Sorry, there was an error uploading your files. Please try again.', 'assistant');
        }

        closeUploadModal();
    });

    // Backend API call for file saving
    async function sendFilesToBackend(files) {
        const ok = await ensureApiKey();
        if (!ok) throw new Error('KEY_REQUIRED');
        const formData = new FormData();

        files.forEach(file => {
            formData.append('files', file);
        });

        const response = await fetchWithSession('/files-upload', {
            method: 'POST',
            body: formData
        });

        if (response.status === 401) {
            throw new Error('KEY_401');
        }

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        return await response.json();
    }

    // Handle the response from backend after file upload
    function handleUploadResponse(uploadResult) {
        let successCount = 0;
        let failureCount = 0;
        let failureMessages = [];

        // Count successes and failures
        for (const [filename, result] of Object.entries(uploadResult)) {
            if (result.Saved === true) {
                successCount++;
            } else {
                failureCount++;
                failureMessages.push(`${filename} --> ${result.Context}`);
            }
        }

        // Create response message based on results
        let responseMessage = '';
        if (successCount > 0 && failureCount === 0) {
            // All files succeeded
            responseMessage = `Successfully uploaded ${successCount} file${successCount > 1 ? 's' : ''}! I can now help you analyze your notebooks${successCount > 1 ? 's' : ''}.`;
        } else if (successCount === 0 && failureCount > 0) {
            // All files failed
            responseMessage = `Failed to upload ${failureCount} file${failureCount > 1 ? 's' : ''}:\n${failureMessages.join('\n')}`;
        } else {
            // Mixed results
            responseMessage = `Upload completed: ${successCount} file${successCount > 1 ? 's' : ''} succeeded, ${failureCount} failed.\n`;
            if (failureMessages.length > 0) {
                responseMessage += `Failures:\n${failureMessages.join('\n')}`;
            }
            responseMessage += `. I can now help you analyze your notebooks - just type '/start' command \n`
        }

        // Show the response message
        setTimeout(() => {
            addMessage(responseMessage, 'assistant');
        }, 500);
    }

    // Click on drop area to trigger file input
    dropArea.addEventListener('click', () => {
        fileInput.click();
    });

    // -- Drag and drop functionality -- 
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropArea.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, unhighlight, false);
    });

    function highlight() {
        dropArea.classList.add('active');
    }

    function unhighlight() {
        dropArea.classList.remove('active');
    }

    dropArea.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    }

    // Refresh page functionality
    refreshBtn.addEventListener('click', async () => {
        const refreshIcon = refreshBtn.querySelector('i');
        refreshIcon.classList.add('fa-spin');
        try {
            // Call backend to clear chat history
            const response = await fetchWithSession('/clear-chat-history', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                // Clear frontend chat messages except welcome message
                setTimeout(() => {
                    while (chatMessages.children.length > 1) {
                        chatMessages.removeChild(chatMessages.lastChild);
                    }
                    window.alert("Chat history has been cleared.");
                    refreshIcon.classList.remove('fa-spin');
                }, 800);
            } else {
                throw new Error('Failed to clear chat history');
            }
        } catch (error) {
            console.error('Error clearing chat history:', error);
            window.alert('Failed to clear chat history. Please try again.');
            refreshIcon.classList.remove('fa-spin');
        }
    });

    // Share functionality, share modal show
    shareBtn.addEventListener('click', () => {
        shareLink.value = window.location.href;
        shareModal.classList.remove('hidden');
        shareModal.setAttribute('aria-hidden', 'false');
        setTimeout(() => {
            shareModal.classList.add('show');
        }, 10);
    });

    // Share modal close
    closeShare.addEventListener('click', () => {
        shareModal.classList.remove('show');
        setTimeout(() => {
            shareModal.classList.add('hidden');
            shareModal.setAttribute('aria-hidden', 'true');
        }, 300);
    });

    // Change link icon from copy to check when link is copied
    copyLink.addEventListener('click', () => { 
        shareLink.select()
        document.execCommand('copy');
        
        // Show copy link confirmation
        const originalIcon = copyLink.innerHTML;
        copyLink.innerHTML = '<i class="fas fa-check"></i>';
        copyLink.classList.add('copy-flash');

        setTimeout(() => {
            copyLink.innerHTML = originalIcon;
            copyLink.classList.remove('copy-flash');
        }, 2000);
    });
});