  
// Wait for DOM to be fully loaded before mounting Vue
document.addEventListener('DOMContentLoaded', function() {
  // Check if Vue is available
  if (typeof Vue === 'undefined') {
    console.error('Vue is not loaded!');
    return;
  }
  
  const { createApp, ref, computed, watch, nextTick, onMounted } = Vue;
  
  // Check if the app container exists
  const appContainer = document.getElementById('app');
  if (!appContainer) {
    console.error('Vue app container (#app) not found!');
    return;
  }
  
  console.log('Mounting Vue app...');
  
  createApp({
    setup() {
      if (typeof marked !== 'undefined' && marked.setOptions) {
        try {
          marked.setOptions({ breaks: true })
        } catch (_) {
          /* older / newer marked builds differ */
        }
      }

      function readInitialModeFromUrl() {
        try {
          const p = new URLSearchParams(window.location.search)
          if (p.get('mode') === 'database' && p.get('paper')) return 'database'
        } catch (_) {}
        return 'review'
      }

      const mode = ref(readInitialModeFromUrl())
      const file = ref(null)
      const isDragging = ref(false)
      const isProcessing = ref(false)
      const isGenerating = ref(false)
      const isDiscussing = ref(false)
      const isAskingFollowUp = ref(false)
      const progress = ref(0)
      const statusMessage = ref('')
      const paperPoints = ref([])
      const prompt = ref('')
      const generatedPoints = ref([])
      const discussion = ref('')
      const followUpQuestion = ref('')
      const usedPaperIds = ref([]) // Track which papers were used for points

      const writeCandidates = ref([])
      const isSuggestingWrite = ref(false)
      const writeSuggestHint = ref('')

      // Database viewer state
      const loadingDatabase = ref(false)
      const databaseError = ref('')
      const papers = ref([])
      const searchQuery = ref('')
      const hasApiKey = ref(false)
      const sessionId = ref('')
      const showKeyModal = ref(false)
      const apiKeyInput = ref('')
      const keyModalError = ref('')

      const formatPoint = (point) => {
        if (point.formatted_text) {
          return point.formatted_text
            .replace('•', '<span class="point-bullet">•</span>')
            .replace(/\(Source:(.*?)\)/, '<span class="point-source">$1</span>');
        }
        return `${point.text} <span class="point-source">${point.source}</span>`;
      }

      /** Full API origin override when HTML is not served by FastAPI (cookies need CORS). No trailing slash. */
      function getApiBase() {
        try {
          const m = document.querySelector('meta[name="api-base-url"]')
          const raw = m != null && m.getAttribute('content') != null ? String(m.getAttribute('content')).trim() : ''
          return raw.replace(/\/$/, '')
        } catch (_) {
          return ''
        }
      }

      /** Path between origin and route (e.g. /api/v1) when behind a path-based reverse proxy. */
      function getApiPathPrefix() {
        try {
          const m = document.querySelector('meta[name="api-path-prefix"]')
          const raw = m != null && m.getAttribute('content') != null ? String(m.getAttribute('content')).trim() : ''
          if (!raw) return ''
          const p = raw.startsWith('/') ? raw : '/' + raw
          return p.replace(/\/$/, '')
        } catch (_) {
          return ''
        }
      }

      /** When set (e.g. Render origin), long-running POST /upload bypasses Vercel and hits this host directly. */
      function getDirectApiBase() {
        try {
          const m = document.querySelector('meta[name="direct-api-base-url"]')
          const raw = m != null && m.getAttribute('content') != null ? String(m.getAttribute('content')).trim() : ''
          if (raw) return raw.replace(/\/$/, '')
        } catch (_) {
          /* ignore */
        }
        try {
          const h = typeof window !== 'undefined' && window.location ? window.location.hostname : ''
          if (h.endsWith('vercel.app')) {
            return 'https://researchassitant-pdf2md-orbs-unified.onrender.com'
          }
        } catch (_) {
          /* ignore */
        }
        return ''
      }

      /**
       * Absolute URL for API calls so the document base tag cannot send requests to the wrong path.
       */
      function resolveApiUrl(urlPath) {
        const path = urlPath.startsWith('/') ? urlPath : '/' + urlPath
        const configured = getApiBase()
        if (configured) {
          return `${configured}${path}`
        }
        const prefix = getApiPathPrefix()
        if (typeof window !== 'undefined' && window.location && window.location.origin) {
          return `${window.location.origin}${prefix}${path}`
        }
        return path
      }

      async function fetchWithSession(urlPath, options = {}) {
        return fetch(resolveApiUrl(urlPath), { credentials: 'include', ...options })
      }

      /** POST /upload (and similar) without Vercel edge timeouts: same backend via direct-api-base-url + session header. */
      async function fetchUploadWithSession(urlPath, options = {}) {
        const direct = getDirectApiBase()
        const path = urlPath.startsWith('/') ? urlPath : '/' + urlPath
        const url = direct ? `${direct}${path}` : resolveApiUrl(urlPath)
        const baseHeaders = { ...(options.headers || {}) }
        if (direct && sessionId.value) {
          baseHeaders['X-RA-Session-Id'] = sessionId.value
        }
        const credentials = direct ? 'omit' : 'include'
        return fetch(url, { ...options, headers: baseHeaders, credentials })
      }

      async function refreshSessionStatus() {
        try {
          const res = await fetchWithSession('/session/status', { headers: { 'Accept': 'application/json' } })
          if (!res.ok) return
          const data = await res.json()
          hasApiKey.value = !!data.has_api_key
          if (data.session_id) sessionId.value = String(data.session_id)
        } catch (_) {
          // ignore
        }
      }

      function closeKeyModal() {
        showKeyModal.value = false
        apiKeyInput.value = ''
        keyModalError.value = ''
      }

      function promptForApiKey() {
        keyModalError.value = ''
        showKeyModal.value = true
      }

      async function saveApiKey() {
        keyModalError.value = ''
        const key = (apiKeyInput.value || '').trim()
        if (!key) return
        const res = await fetchWithSession('/session/api-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ api_key: key })
        })
        if (!res.ok) {
          keyModalError.value = await res.text().catch(() => 'Failed to set key')
          return
        }
        try {
          const data = await res.json()
          hasApiKey.value = !!data.has_api_key
          if (data.session_id) sessionId.value = String(data.session_id)
        } catch (_) {
          hasApiKey.value = true
        }
        closeKeyModal()
      }

      async function ensureApiKey() {
        await refreshSessionStatus()
        if (!hasApiKey.value) {
          promptForApiKey()
          throw new Error('OpenRouter API key required')
        }
      }

      async function endSession() {
        await fetchWithSession('/session/end', { method: 'POST' }).catch(() => {})
        hasApiKey.value = false
        window.location.reload()
      }
      
      const statusClass = computed(() => {
        if (statusMessage.value.includes('success')) return 'success'
        if (statusMessage.value.includes('Error') || statusMessage.value.includes('Failed')) return 'error'
        return 'processing'
      })
      
      /** Generate enabled: always when prompt OK if no candidate panel; otherwise need ≥1 checked box. */
      const canGenerateWritePoints = computed(() => {
        if (!prompt.value.trim()) return false
        if (isGenerating.value || isSuggestingWrite.value) return false
        if (!writeCandidates.value.length) return true
        return writeCandidates.value.some((c) => c.checked)
      })

      /** Turn numeric citations into green chips (order = usedPaperIds / discussion paper list). Skips pre/code blocks. */
      function linkifyDiscussionCitations(html, paperIdsOrdered) {
        const ids = paperIdsOrdered || []
        if (!ids.length || !html) return html
        const maxIdx = ids.length

        function citeSpan(n, innerHtml) {
          const pid = ids[n - 1]
          if (!pid) return innerHtml
          const safe = String(pid).replace(/"/g, '&quot;')
          return `<span class="citation discussion-cite" data-paper-id="${safe}" role="link" tabindex="0" title="Open paper in database (new tab)">${innerHtml}</span>`
        }

        function inject(chunk) {
          let s = chunk
          // Replace "Paper 1" style refs with placeholders first so we never match inside the span we add.
          const paperSlots = []
          s = s.replace(/\bPapers?\s+(\d{1,3})\b/gi, (full, numStr) => {
            const n = parseInt(numStr, 10)
            if (n < 1 || n > maxIdx) return full
            const token = `\uE000${paperSlots.length}PAPERCITE\uE001`
            paperSlots.push({ token, n, full })
            return token
          })
          s = s.replace(/\[\s*(\d+)\s*\]/g, (full, numStr) => {
            const n = parseInt(numStr, 10)
            if (n < 1 || n > maxIdx) return full
            return citeSpan(n, `[${n}]`)
          })
          s = s.replace(/\((\d{1,3})\)/g, (full, numStr) => {
            const n = parseInt(numStr, 10)
            if (n < 1 || n > maxIdx) return full
            return citeSpan(n, `(${n})`)
          })
          for (const { token, n, full } of paperSlots) {
            s = s.split(token).join(citeSpan(n, full))
          }
          return s
        }

        const skipBlocks = /(<(?:pre|code)\b[^>]*>[\s\S]*?<\/(?:pre|code)>)/gi
        const parts = html.split(skipBlocks)
        return parts.map((part) => (/^<(pre|code)\b/i.test(part) ? part : inject(part))).join('')
      }

      const discussionHtml = computed(() => {
        const raw = discussion.value || ''
        if (!raw.trim()) return ''
        const ids = usedPaperIds.value || []
        try {
          if (typeof marked !== 'undefined' && typeof marked.parse === 'function') {
            const inner = marked.parse(raw)
            return linkifyDiscussionCitations(inner, ids)
          }
        } catch (e) {
          console.warn('Discussion markdown render failed', e)
        }
        return linkifyDiscussionCitations(
          String(raw)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;'),
          ids
        )
      })

      function openDatabasePaperInNewTab(paperId) {
        if (!paperId || typeof window === 'undefined') return
        try {
          const u = new URL('/app/research/', window.location.origin)
          u.searchParams.set('mode', 'database')
          u.searchParams.set('paper', paperId)
          const w = window.open(u.toString(), '_blank', 'noopener,noreferrer')
          if (!w) navigateToSource(paperId)
        } catch (_) {
          navigateToSource(paperId)
        }
      }

      function onDiscussionCitationClick(e) {
        const el = e.target && e.target.closest && e.target.closest('.discussion-cite[data-paper-id]')
        if (!el) return
        const id = el.getAttribute('data-paper-id')
        if (id) openDatabasePaperInNewTab(id)
      }

      function onDiscussionCitationKeydown(e) {
        if (e.key !== 'Enter' && e.key !== ' ') return
        const el = e.target && e.target.closest && e.target.closest('.discussion-cite[data-paper-id]')
        if (!el) return
        e.preventDefault()
        const id = el.getAttribute('data-paper-id')
        if (id) openDatabasePaperInNewTab(id)
      }

      const filteredPapers = computed(() => {
        if (!searchQuery.value) return papers.value;
        
        const query = searchQuery.value.toLowerCase();
        return papers.value.filter(paper => {
          const filename = (paper.filename || '').toLowerCase();
          const content = paper.fullContent ? paper.fullContent.toLowerCase() : '';
          const points = paper.points ? paper.points.join(' ').toLowerCase() : '';
          
          return filename.includes(query) || 
                 content.includes(query) || 
                 points.includes(query);
        });
      });
      
      function setMode(newMode) {
        mode.value = newMode
        if (newMode === 'review') {
          generatedPoints.value = []
          discussion.value = ''
          prompt.value = ''
          writeCandidates.value = []
          writeSuggestHint.value = ''
          usedPaperIds.value = []
          followUpQuestion.value = ''
        } else if (newMode === 'database') {
          loadDatabase()
        } else {
          paperPoints.value = []
          statusMessage.value = ''
        }
      }

      function selectAllWriteCandidates() {
        writeCandidates.value.forEach((c) => { c.checked = true })
      }

      function deselectAllWriteCandidates() {
        writeCandidates.value.forEach((c) => { c.checked = false })
      }

      async function suggestWriteSources() {
        if (!prompt.value.trim()) return
        try { await ensureApiKey() } catch (_) { return }
        isSuggestingWrite.value = true
        writeSuggestHint.value = ''
        writeCandidates.value = []
        try {
          const payload = JSON.stringify({ prompt: prompt.value.trim() })
          const tryEndpoints = ['/research/candidates', '/write/candidates']
          let response = null
          for (const ep of tryEndpoints) {
            response = await fetchWithSession(ep, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: payload
            })
            if (response.ok) break
            if (response.status !== 404) {
              const t = await response.text()
              throw new Error((t || response.statusText || 'Request failed').slice(0, 400))
            }
          }
          if (!response || !response.ok) {
            const tried = tryEndpoints.map((e) => resolveApiUrl(e)).join(' · ')
            throw new Error(
              `API returned 404 for suggest-sources (${tried}). Redeploy the latest backend, or set meta api-base-url / api-path-prefix if the API is not on this origin.`
            )
          }
          const data = await response.json()
          if (data.status !== 'success') {
            throw new Error(data.message || 'Unexpected response')
          }
          const list = data.candidates || []
          writeCandidates.value = list.map((c) => ({
            ...c,
            checked: !!c.llm_include
          }))
          if (!writeCandidates.value.length) {
            writeSuggestHint.value = 'No papers in the database yet. Upload PDFs in Review mode, then try again.'
          } else {
            writeSuggestHint.value = 'Adjust checkboxes if needed, then run Generate. Discussion and follow-ups use the same paper set after you generate points.'
          }
        } catch (error) {
          writeSuggestHint.value = `Could not suggest sources: ${error.message || error}`
        } finally {
          isSuggestingWrite.value = false
        }
      }
      
      function formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes'
        const k = 1024
        const sizes = ['Bytes', 'KB', 'MB', 'GB']
        const i = Math.floor(Math.log(bytes) / Math.log(k))
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
      }
      
      function formatBytes(bytes) {
          if (bytes === 0) return '0 Bytes'
          const k = 1024
          const sizes = ['Bytes', 'KB', 'MB', 'GB']
          const i = Math.floor(Math.log(bytes) / Math.log(k))
          return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
      }
      
      function triggerFileInput() {
        document.getElementById('fileInput').click()
      }
      
      function handleFileSelect(event) {
        if (event.target.files.length) {
          file.value = event.target.files[0]
        }
      }
      
      function handleDragOver(event) {
        isDragging.value = true
        event.dataTransfer.dropEffect = 'copy'
      }
      
      function handleDrop(event) {
        isDragging.value = false
        const files = event.dataTransfer.files
        if (files.length && files[0].type === 'application/pdf') {
          file.value = files[0]
        } else {
          statusMessage.value = 'Please drop a PDF file'
        }
      }
      
      async function processPaper() {
        if (!file.value) return

        try { await ensureApiKey() } catch (_) { return }
        
        isProcessing.value = true
        progress.value = 30
        statusMessage.value = 'Uploading and processing paper...'
        
        try {
          const formData = new FormData()
          formData.append('file', file.value)
          
          const response = await fetchUploadWithSession('/upload', {
              method: 'POST',
              body: formData
          })
          
          progress.value = 70
          
          if (!response.ok) {
              const error = await response.text()
              throw new Error(error || 'Failed to process paper')
          }
          
          const data = await response.json()
          statusMessage.value = 'Paper processed successfully! Extracted key points.'
          
          paperPoints.value = data.points.map(point => ({
              formatted_text: point.text,
              raw_data: {
                  text: point.text,
                  source: data.filename || 'Current Paper',
                  sourceId: data.id
              }
          }))
        } catch (error) {
          let tip = ''
          try {
            const host = typeof window !== 'undefined' && window.location ? window.location.hostname : ''
            if (host.endsWith('vercel.app') && !getDirectApiBase()) {
              tip =
                ' Tip: large PDFs often hit Vercel proxy timeouts—set meta direct-api-base-url to your Render API URL (see docs/vercel-proxy-verify.md).'
            }
          } catch (_) {}
          statusMessage.value = `Error: ${error.message || 'Unknown error occurred'}${tip}`
        } finally {
          progress.value = 100
          setTimeout(() => {
              isProcessing.value = false
              progress.value = 0
          }, 500)
        }
      }
      
      async function generatePoints() {
        if (!prompt.value.trim()) return

        const hasPanel = writeCandidates.value.length > 0
        const selectedIds = writeCandidates.value.filter((c) => c.checked).map((c) => c.id)
        if (hasPanel && !selectedIds.length) {
          writeSuggestHint.value = 'Select at least one paper (checkboxes), use Select all, or refresh the page and generate without using Suggest.'
          return
        }

        try { await ensureApiKey() } catch (_) { return }
        
        isGenerating.value = true
        progress.value = 30
        generatedPoints.value = []
        discussion.value = ''
        usedPaperIds.value = []
        
        try {
          const genBody = hasPanel
            ? { prompt: prompt.value, paper_ids: selectedIds }
            : { prompt: prompt.value }

          const response = await fetchWithSession('/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(genBody)
          })
          
          progress.value = 70
          
          if (!response.ok) {
            throw new Error('Failed to generate points')
          }
          
          const data = await response.json()
          
          generatedPoints.value = data.points.map(point => {
            const raw = point.raw_data || {}
            const ids = Array.isArray(raw.sourceIds) && raw.sourceIds.length
              ? raw.sourceIds
              : (raw.sourceId ? [raw.sourceId] : (point.sourceId ? [point.sourceId] : []))
            return {
              text: point.formatted_text || point.text,
              source: raw.source || point.source,
              sourceId: ids[0] || null,
              sourceIds: ids
            }
          })
          
          // Store the paper IDs used for these points
          if (data.paper_ids && data.paper_ids.length) {
            usedPaperIds.value = data.paper_ids
          } else if (hasPanel && selectedIds.length) {
            usedPaperIds.value = selectedIds
          }
        } catch (error) {
          generatedPoints.value = [{
            text: `Error: ${error.message || 'Failed to generate points'}`,
            source: null,
            sourceId: null
          }]
        } finally {
          progress.value = 100
          setTimeout(() => {
            isGenerating.value = false
            progress.value = 0
          }, 500)
        }
      }
      
      async function startDiscussion() {
        if (!prompt.value.trim() || !generatedPoints.value.length) return

        try { await ensureApiKey() } catch (_) { return }
        
        isDiscussing.value = true
        discussion.value = '*Analyzing research points and generating discussion…*'
        
        try {
          const response = await fetchWithSession('/discuss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
              prompt: prompt.value,
              paper_ids: usedPaperIds.value // Pass the papers used for points
            })
          })
          
          if (!response.ok) {
            throw new Error('Failed to start discussion')
          }
          
          const data = await response.json()
          if (Array.isArray(data.paper_ids) && data.paper_ids.length) {
            usedPaperIds.value = data.paper_ids
          }
          discussion.value = data.discussion || 'No discussion generated'
        } catch (error) {
          discussion.value = `Error: ${error.message || 'Failed to generate discussion'}`
        } finally {
          isDiscussing.value = false
        }
      }

      async function askFollowUp() {
        if (!followUpQuestion.value.trim()) return

        try { await ensureApiKey() } catch (_) { return }
        
        isAskingFollowUp.value = true
        const originalDiscussion = discussion.value
        discussion.value = `${originalDiscussion}\n\n### Follow-up question\n${followUpQuestion.value}\n\n### Answer\n*Thinking…*`
        
        try {
          const response = await fetchWithSession('/discuss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              prompt: followUpQuestion.value,
              context: originalDiscussion,
              paper_ids: usedPaperIds.value
            })
          })
          
          if (!response.ok) {
            throw new Error('Failed to get follow-up answer')
          }
          
          const data = await response.json()
          if (Array.isArray(data.paper_ids) && data.paper_ids.length) {
            usedPaperIds.value = data.paper_ids
          }
          discussion.value = `${originalDiscussion}\n\n### Follow-up question\n${followUpQuestion.value}\n\n### Answer\n${data.discussion}`
          followUpQuestion.value = ''
        } catch (error) {
          discussion.value = `${originalDiscussion}\n\n### Follow-up question\n${followUpQuestion.value}\n\n### Error\n${error.message || 'Failed to answer'}`
        } finally {
          isAskingFollowUp.value = false
        }
      }
      
      async function loadDatabase() {
        try {
          loadingDatabase.value = true;
          databaseError.value = '';
          
          const response = await fetchWithSession('/database', {
              headers: { 'Accept': 'application/json' }
          });

          const contentType = response.headers.get('content-type');
          if (!contentType?.includes('application/json')) {
              const errorText = await response.text();
              throw new Error(`Server returned HTML: ${errorText.substring(0, 50)}`);
          }

          const data = await response.json();
          
          if (data.status !== "success") {
              throw new Error(data.message || "Unknown error");
          }

          papers.value = data.papers.map(p => ({
              ...p,
              expanded: false,
              fullContent: "",
              content_length: p.content_length || 0
          }));
          
        } catch (error) {
          databaseError.value = `Failed to load: ${error.message}`;
          console.error("Database load error:", error);
        } finally {
          loadingDatabase.value = false;
        }
      }
      
      async function togglePaperDetails(paperId) {
        const paper = papers.value.find(p => p.id === paperId);
        if (!paper) return;
        
        paper.expanded = !paper.expanded;
        
        if (paper.expanded && !paper.fullContent) {
          await loadPaperDetails(paper);
        }
      }
      
      async function loadPaperDetails(paper) {
        try {
          paper.detailsLoading = true;
          paper.detailsError = '';
          
          const response = await fetchWithSession(`/database/${paper.id}`);
          
          if (!response.ok) {
            throw new Error('Failed to load paper details')
          }
          
          const data = await response.json();
          paper.fullContent = data.content;
          paper.points = Array.isArray(data.points) ? data.points : [];
        } catch (error) {
          paper.detailsError = `Error: ${error.message || 'Unknown error occurred'}`;
        } finally {
          paper.detailsLoading = false;
        }
      }
      
      async function revealPaperInDatabase(paperId) {
        if (!paperId) return
        const paper = papers.value.find((p) => p.id === paperId)
        if (!paper) {
          statusMessage.value = 'Could not find that paper in the database (it may have been removed).'
          return
        }
        if (!paper.expanded) {
          paper.expanded = true
          await loadPaperDetails(paper)
        } else if (!paper.fullContent) {
          await loadPaperDetails(paper)
        }
        await nextTick()
        const card = document.querySelector(`[data-paper-id="${paperId}"]`)
        card?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        card?.classList.add('paper-card--highlight')
        window.setTimeout(() => card?.classList.remove('paper-card--highlight'), 2600)
      }

      async function navigateToSource(paperId) {
        if (!paperId) return
        searchQuery.value = ''
        mode.value = 'database'
        await loadDatabase()
        await nextTick()
        await revealPaperInDatabase(paperId)
      }
      
      function stemToMdDownloadName(filename) {
        let s = String(filename || 'paper').trim()
        s = s.replace(/[/\\]/g, '_')
        if (/\.pdf$/i.test(s)) s = s.slice(0, -4)
        else {
          const dot = s.lastIndexOf('.')
          if (dot > 0) s = s.slice(0, dot)
        }
        s = s.replace(/[<>:"|?*]+/g, '_').trim() || 'paper'
        return `${s}.md`
      }

      async function downloadMarkdown(paperId, filename) {
        try {
          const response = await fetchWithSession(`/download/${paperId}`);
          
          if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.message || 'Failed to download file');
          }
          
          const blob = await response.blob();
          const url = window.URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;

          let mdFilename = stemToMdDownloadName(filename)
          const cd = response.headers.get('content-disposition')
          if (cd) {
            const mStar = cd.match(/filename\*=UTF-8''([^;\n]+)/i)
            if (mStar) {
              try {
                mdFilename = decodeURIComponent(mStar[1].trim().replace(/^"+|"+$/g, ''))
              } catch (_) { /* keep stem */ }
            } else {
              const m = cd.match(/filename="([^"]+)"/i) || cd.match(/filename=([^;\s]+)/i)
              if (m && m[1] && !/^full\.md$/i.test(m[1].trim())) {
                mdFilename = m[1].trim().replace(/^"+|"+$/g, '')
              }
            }
          }
          if (!mdFilename.toLowerCase().endsWith('.md')) {
            mdFilename = stemToMdDownloadName(mdFilename)
          }
          
          a.download = mdFilename;
          document.body.appendChild(a);
          a.click();
          window.URL.revokeObjectURL(url);
          a.remove();
        } catch (error) {
          statusMessage.value = `Download failed: ${error.message}`;
        }
      }

      async function deletePaper(paperId) {
        if (!paperId) return
        if (!window.confirm('Remove this paper from the database? Files on disk for this upload will be deleted. This cannot be undone.')) {
          return
        }
        databaseError.value = ''
        try {
          const response = await fetchWithSession(`/database/${encodeURIComponent(paperId)}`, {
            method: 'DELETE',
            headers: { Accept: 'application/json' }
          })
          const data = await response.json().catch(() => ({}))
          if (!response.ok) {
            const d = data.detail
            const msg = typeof d === 'string' ? d : (Array.isArray(d) ? d.map((x) => x.msg || x).join('; ') : (data.message || response.statusText))
            throw new Error(msg || 'Delete failed')
          }
          papers.value = papers.value.filter((p) => p.id !== paperId)
          statusMessage.value = 'Paper removed from the database.'
        } catch (error) {
          databaseError.value = error.message || 'Failed to delete paper'
        }
      }

      async function deleteAllPapers() {
        if (!papers.value.length) return
        if (!window.confirm(`Delete all ${papers.value.length} paper(s) from the database? This cannot be undone.`)) {
          return
        }
        databaseError.value = ''
        try {
          const response = await fetchWithSession('/database', {
            method: 'DELETE',
            headers: { Accept: 'application/json' }
          })
          const data = await response.json().catch(() => ({}))
          if (!response.ok) {
            const d = data.detail
            const msg = typeof d === 'string' ? d : (Array.isArray(d) ? d.map((x) => x.msg || x).join('; ') : (data.message || response.statusText))
            throw new Error(msg || 'Delete failed')
          }
          papers.value = []
          statusMessage.value = data.deleted != null
            ? `Removed ${data.deleted} paper(s) from the database.`
            : 'Database cleared.'
        } catch (error) {
          databaseError.value = error.message || 'Failed to delete all papers'
        }
      }
      
      watch(() => mode.value, (newMode) => {
        if (newMode === 'database' && papers.value.length === 0) {
          loadDatabase();
        }
      });

      onMounted(() => {
        nextTick(async () => {
          try {
            const params = new URLSearchParams(window.location.search)
            const pid = params.get('paper')
            if (params.get('mode') !== 'database' || !pid) return
            if (!papers.value.length) await loadDatabase()
            await nextTick()
            await revealPaperInDatabase(pid)
            try {
              const u = new URL(window.location.href)
              u.searchParams.delete('paper')
              u.searchParams.delete('mode')
              const qs = u.searchParams.toString()
              window.history.replaceState({}, '', u.pathname + (qs ? '?' + qs : '') + u.hash)
            } catch (_) {}
          } catch (e) {
            console.warn('Database deep link:', e)
          }
        })
      })

      // On first load, initialize session + optionally request key
      refreshSessionStatus().then(() => {
        if (!hasApiKey.value) promptForApiKey()
      })
      
      return {
        mode,
        file,
        isDragging,
        isProcessing,
        isGenerating,
        isDiscussing,
        isAskingFollowUp,
        isSuggestingWrite,
        progress,
        statusMessage,
        paperPoints,
        prompt,
        generatedPoints,
        discussion,
        discussionHtml,
        followUpQuestion,
        usedPaperIds,
        writeCandidates,
        writeSuggestHint,
        canGenerateWritePoints,
        statusClass,
        loadingDatabase,
        databaseError,
        papers,
        filteredPapers,
        searchQuery,
        setMode,
        formatFileSize,
        formatBytes,
        triggerFileInput,
        handleFileSelect,
        handleDragOver,
        handleDrop,
        processPaper,
        generatePoints,
        suggestWriteSources,
        selectAllWriteCandidates,
        deselectAllWriteCandidates,
        startDiscussion,
        askFollowUp,
        loadDatabase,
        togglePaperDetails,
        navigateToSource,
        downloadMarkdown,
        deletePaper,
        deleteAllPapers,
        onDiscussionCitationClick,
        onDiscussionCitationKeydown,
        formatPoint,
        promptForApiKey,
        endSession,
        showKeyModal,
        apiKeyInput,
        keyModalError,
        closeKeyModal,
        saveApiKey
      }
    }
  }).mount('#app');

  console.log('Vue app mounted successfully');
});
