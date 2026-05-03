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
        setTimeout(() => keyModal.classList.add('show'), 10);
        if (apiKeyInput) apiKeyInput.focus();
        return false; // saving happens via modal buttons
    }

    async function ensureApiKey() {
        const has = await sessionHasKey();
        if (has) return true;
        await promptForApiKey();
        return false;
    }

    async function endSession() {
        await fetchWithSession('/session/end', { method: 'POST' }).catch(() => {});
        window.location.reload();
    }

    function closeKeyModal() {
        if (!keyModal) return;
        keyModal.classList.remove('show');
        setTimeout(() => keyModal.classList.add('hidden'), 200);
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

    // Initialize session + optionally request key
    ensureApiKey();


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
        hasUserTyped = (messageInput.value.trim() === ''); // either true or false 
        sendBtn.disabled = hasUserTyped;
        if (sendBtn.disabled) {
            sendBtn.classList.remove('text-blue-500');
            sendBtn.classList.add('text-gray-500');
        } else {
            sendBtn.classList.remove('text-gray-500');
            sendBtn.classList.add('text-blue-500');
        }
    });

    // --- Send message functions ---
    // sendMessage calls addMessage to display message on the page
    // fetchChatbotAnswer to get the response from the backend API
    const sendMessage = () => {
        // Get message from input and trim whitespace
        // If empty, user is cringe and not allowed to send anything
        const message = messageInput.value.trim();
        if (message === '') return;
        // If chatbot is still answering, user is cringe x2  
        // and not allowed to send another message
        if (chatbotAnswered == false) return;
        chatbotAnswered = false;

        if ((message.toLowerCase() === "/start") || (message.toLowerCase() === "start") || 
        (message.toLowerCase() === "/next") || (message.toLowerCase() === "next")) {
            console.log("Starting analysis of the notebook")

            addMessage(message, 'user');

            messageInput.value = '';
            sendBtn.disabled = true;
            sendBtn.classList.remove('text-blue-500');
            sendBtn.classList.add('text-gray-500');
            // Trigger sending request to analysis endpoint
            fetchChatbotAnalysis(message)
        } 
        else {
            // Add user message to chat
            // Using addMessage function, this function is
            // going to create and insert html <div></div> block 
            // that contains the message (either by AI or user) to the html page (index.html)
            addMessage(message, 'user');

            // Clear input and disable send button, basically resetting the input field
            // and making sure the user can't send empty messages 
            // (disable button until user types something, 
            // JS listens to any input, messageInput.addEventListener)
            messageInput.value = '';
            sendBtn.disabled = true;
            sendBtn.classList.remove('text-blue-500');
            sendBtn.classList.add('text-gray-500');

            // Trigger chatbot answer fetch
            fetchChatbotAnswer(message);
        }
    };
    // Backend API call for handling messages
    async function fetchChatbotAnswer(message) {
        try {
            const ok = await ensureApiKey();
            if (!ok) return;
            // Send POST request to the backend API
            const response = await fetchWithSession('/chatbot-answer', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ role: 'user', content: message }) // Send message as JSON
            });

            if (response.ok == false) {
                throw new Error('Something went wrong with the request');
            }

            const botMessage = await response.json();
            // Add bot response to chat
            addMessage(botMessage.content, 'assistant');
            chatbotAnswered = true;
        } catch (error) {
            console.error('Error fetching chatbot answer:', error);
            // Add error message to chat
            addMessage('Sorry, I could not process your request. Please try again later.', 'assistant');
        }
    }
    // Backend API call for handling /start command
    async function fetchChatbotAnalysis(message) {
        try {
            const ok = await ensureApiKey();
            if (!ok) return;
            // Send POST request to the backend API
            const response = await fetchWithSession('/analyze', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ role: 'user', content: message }) // Send message as JSON
            });

            if (response.ok == false) {
                throw new Error('Something went wrong with the request');
            }

            const botMessage = await response.json();
            // Add bot response to chat
            addMessage(botMessage.content, 'assistant');
            chatbotAnswered = true;
        } catch (error) {
            console.error('Error fetching chatbot answer:', error);
            // Add error message to chat
            addMessage('Sorry, I could not process your request. Please try again later.', 'assistant');
        }
    }


    // -- Send message on button click -- 
    sendBtn.addEventListener('click', sendMessage);

    // --- Add message to chat ----
    const addMessage = (text, sender) => {
        // Create a new message <div> element in memory
        // This will the class (container) of the new message added
        // We apply styling depending on the sender
        const messageDiv = document.createElement('div');
        messageDiv.className = sender === 'user' ? 'flex flex-row-reverse mb-4' : 'flex mb-4';

        // The following connection variables determine the appearance of the message
        // Depending on the sender (user or not-user), different classes are applied to the message
        // Keep in mind the syntax of ternary operators: value ? valueIfTrue : valueIfFalse
        const iconClass = (sender === 'user' ? 'fa-user' : 'fa-robot');
        const iconBgClass = (sender === 'user' ? 'bg-gray-200' : 'bg-blue-100');
        const iconColorClass = (sender === 'user' ? 'text-gray-500' : 'text-blue-500');
        const marginClass = (sender === 'user' ? 'ml-2' : 'mr-2');
        const messageClass = (sender === 'user' ? 'message user-message p-3 shadow-sm' : 'message assistant-message p-3 shadow-sm');

        const markdownContent = text

        // Html structure prototype
        if (sender == "user") {
            let textHTML = text.replace("\n", "<br>");
            messageDiv.innerHTML = `
                <div class="w-8 h-8 rounded-full ${iconBgClass} flex items-center justify-center ${marginClass} flex-shrink-0">
                    <i class="fas ${iconClass} ${iconColorClass} text-sm"></i>
                </div>
                <div class="${messageClass}">
                    <p>${textHTML}</p>
                </div>
            `;
        } else {
            messageDiv.innerHTML = `
                <div class="w-8 h-8 rounded-full ${iconBgClass} flex items-center justify-center ${marginClass} flex-shrink-0">
                    <i class="fas ${iconClass} ${iconColorClass} text-sm"></i>
                </div>
                <div class="${messageClass}">
                    ${marked.parse(markdownContent)}
                </div>
            `;
        }

        // Append the message to chat messages container
        // Then scroll to the bottom of the chat container
        chatMessages.appendChild(messageDiv);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    };

    // File upload modal 
    // Show upload modal, i.e. remove the view property hidden and add property show
    attachBtn.addEventListener('click', () => {
        uploadModal.classList.remove('hidden');
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
        }, 300);
        resetFileInput(); // make selectedFiles array empty
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
            confirmUpload.classList.add('opacity-50', 'cursor-not-allowed');
            return;
        }

        filePreview.style.display = 'block';
        fileCount.style.display = 'block';
        selectedCount.textContent = selectedFiles.length;
        
        // Clear existing file list
        // This will remove all previous file items from the html preview on the page,
        // just under the drag and drop area
        filesList.innerHTML = '';
        
        // Add each file to the preview
        selectedFiles.forEach((file, index) => {
            const fileItem = document.createElement('div');
            fileItem.className = 'flex items-center justify-between p-2 bg-gray-50 rounded border';
            
            // Get file icon based on file type
            const fileIcon = getFileIcon(file.type);
            
            // We will implement removeFileByIndex function later
            fileItem.innerHTML = `
                <div class="flex items-center flex-1 min-w-0">
                    <i class="fas ${fileIcon} text-blue-500 mr-2 flex-shrink-0"></i>
                    <span class="text-sm text-gray-700 truncate">${file.name}</span>
                    <span class="text-xs text-gray-500 ml-2 flex-shrink-0">(${formatFileSize(file.size)})</span>
                </div>
                <button class="text-gray-500 hover:text-red-500 ml-2 flex-shrink-0" onclick="removeFileByIndex(${index})">
                    <i class="fas fa-times"></i>
                </button>
            `;
            
            filesList.appendChild(fileItem);
        });

        confirmUpload.disabled = false;
        confirmUpload.classList.remove('opacity-50', 'cursor-not-allowed');
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

    // Confirm upload button
    confirmUpload.addEventListener('click', async () => {
        if (selectedFiles.length > 0) {
            // Show uploading message immediately
            if (selectedFiles.length === 1) {
                let fileTitle = selectedFiles[0].name;
                addMessage(`Uploading file: ${fileTitle}...`, 'user');
            } else {
                const fileNames = selectedFiles.map(f => f.name).join(', ');
                let filesLength = selectedFiles.length;
                addMessage(`Uploading ${filesLength} files: ${fileNames}...`, 'user');
            }

            // Actually send files to backend
            try {
                const uploadResult = await sendFilesToBackend(selectedFiles);
                handleUploadResponse(uploadResult);
            } catch (error) {
                console.error('Upload error:', error);
                addMessage('Sorry, there was an error uploading your files. Please try again.', 'assistant');
            }
        }

        closeUploadModal();
    });

    // Backend API call for file saving
    async function sendFilesToBackend(files) {
        const ok = await ensureApiKey();
        if (!ok) throw new Error('OpenRouter API key required');
        const formData = new FormData();
        
        // Add each selected file to FormData
        files.forEach(file => {
            formData.append('files', file);
        });

        const response = await fetchWithSession('/files-upload', {
            method: 'POST',
            body: formData // Don't set Content-Type header, browser will set it with boundary
        });

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
        shareLink.value = window.location.href; // Get current page's link
        shareModal.classList.remove('hidden');
        setTimeout(() => {
            shareModal.classList.add('show');
        }, 10);
    });

    // Share modal close
    closeShare.addEventListener('click', () => {
        shareModal.classList.remove('show');
        setTimeout(() => {
            shareModal.classList.add('hidden');
        }, 300);
    });

    // Change link icon from copy to check when link is copied
    copyLink.addEventListener('click', () => { 
        shareLink.select()
        document.execCommand('copy');
        
        // Show copy link confirmation
        const originalIcon = copyLink.innerHTML;
        copyLink.innerHTML = '<i class="fas fa-check"></i>';
        copyLink.classList.add('text-green-500');
        
        setTimeout(() => {
            copyLink.innerHTML = originalIcon;
            copyLink.classList.remove('text-green-500');
        }, 2000);
    });
});