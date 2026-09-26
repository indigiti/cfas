function attendanceApp() {
  return {
    tab: 'verify',
    busy: false,
    streams: { register: null, verify: null },
    cameraActive: { register: false, verify: false },
    config: {
      test_mode: true,
      geolocation_enabled: false,
      anti_spoofing_enabled: false,
      radius_m: 100,
      max_accuracy_m: 80,
    },
    register: { user_id: '', name: '' },
    verify: { user_id: '' },
    history: [],
    message: { type: 'info', title: '', text: '', metrics: '' },

    async init() {
      try {
        const res = await fetch('/api/config');
        this.config = await res.json();
      } catch (_) {
        this.showMessage('error', 'Server unavailable', 'Could not load application configuration.');
      }
      this.loadHistory();
      this.$nextTick(() => {
        if (window.gsap) gsap.from('.topbar, .notice, .tabs', { opacity: 0, y: -12, stagger: 0.08, duration: 0.5 });
      });
    },

    async startCamera(kind) {
      this.stopCamera(kind);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } },
          audio: false,
        });
        this.streams[kind] = stream;
        this.cameraActive[kind] = true;
        this.$refs[`${kind}Video`].srcObject = stream;
        this.showMessage('info', 'Camera ready', 'Center one face in the frame, then capture.');
      } catch (err) {
        this.showMessage('error', 'Camera blocked', 'Allow camera permission and use HTTPS or localhost.');
      }
    },

    stopCamera(kind) {
      const stream = this.streams[kind];
      if (stream) stream.getTracks().forEach(track => track.stop());
      this.streams[kind] = null;
      this.cameraActive[kind] = false;
    },

    capture(kind) {
      const video = this.$refs[`${kind}Video`];
      const canvas = this.$refs[`${kind}Canvas`];
      if (!video || !video.videoWidth) throw new Error('Start the camera first.');
      const maxWidth = 960;
      const scale = Math.min(1, maxWidth / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      const ctx = canvas.getContext('2d');
      // Mirror the capture so it matches the preview users see.
      ctx.save();
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      ctx.restore();
      return canvas.toDataURL('image/jpeg', 0.9);
    },

    async registerFace() {
      if (!this.register.user_id || !this.register.name) {
        return this.showMessage('error', 'Missing details', 'Enter both User ID and name.');
      }
      let image;
      try { image = this.capture('register'); }
      catch (err) { return this.showMessage('error', 'Camera not ready', err.message); }

      this.busy = true;
      try {
        const data = await this.postJSON('/api/register', { ...this.register, image });
        if (!data.ok) throw new Error(data.message);
        this.showMessage('success', 'Enrollment complete', data.message);
        this.verify.user_id = this.register.user_id;
        this.register = { user_id: '', name: '' };
        this.stopCamera('register');
        this.tab = 'verify';
      } catch (err) {
        this.showMessage('error', 'Registration failed', err.message);
      } finally {
        this.busy = false;
      }
    },

    async verifyFace() {
      if (!this.verify.user_id) {
        return this.showMessage('error', 'User ID required', 'Enter the registered User ID.');
      }
      let image;
      try { image = this.capture('verify'); }
      catch (err) { return this.showMessage('error', 'Camera not ready', err.message); }

      this.busy = true;
      try {
        const location = await this.getLocationIfNeeded();
        const data = await this.postJSON('/api/verify', {
          user_id: this.verify.user_id,
          image,
          ...location,
        });
        if (!data.ok) throw new Error(data.message);

        if (data.verified) {
          const confidence = data.face?.confidence;
          const metrics = [
            confidence != null ? `Confidence: ${Number(confidence).toFixed(1)}%` : '',
            `Location: ${data.location?.status || 'N/A'}`,
          ].filter(Boolean).join(' · ');
          this.showMessage('success', data.duplicate ? 'Verified — already marked' : 'Present', data.message, metrics);
          await this.loadHistory();
        } else {
          const confidence = data.face?.confidence;
          this.showMessage('error', 'Verification rejected', data.message,
            confidence != null ? `Confidence: ${Number(confidence).toFixed(1)}%` : `Location: ${data.location?.status || 'N/A'}`);
        }
      } catch (err) {
        this.showMessage('error', 'Verification failed', err.message);
      } finally {
        this.busy = false;
      }
    },

    async getLocationIfNeeded() {
      if (!this.config.geolocation_enabled) return {};
      return await new Promise((resolve, reject) => {
        if (!navigator.geolocation) return reject(new Error('Geolocation is not supported on this device.'));
        navigator.geolocation.getCurrentPosition(
          pos => resolve({
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy: pos.coords.accuracy,
          }),
          () => reject(new Error('Location permission is required for attendance.')),
          { enableHighAccuracy: true, timeout: 12000, maximumAge: 0 }
        );
      });
    },

    async loadHistory() {
      try {
        const res = await fetch('/api/attendance');
        const data = await res.json();
        this.history = data.rows || [];
      } catch (_) {
        this.history = [];
      }
    },

    async postJSON(url, payload) {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({ ok: false, message: 'Invalid server response.' }));
      if (!res.ok && !data.message) data.message = `Request failed (${res.status})`;
      return data;
    },

    showMessage(type, title, text, metrics = '') {
      this.message = { type, title, text, metrics };
      this.$nextTick(() => {
        if (window.gsap && this.$refs.resultBox) {
          gsap.fromTo(this.$refs.resultBox, { opacity: 0, y: 14, scale: 0.98 }, { opacity: 1, y: 0, scale: 1, duration: 0.28 });
        }
      });
    },

    prettyDate(value) {
      if (!value) return '';
      const d = new Date(value);
      return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
    },
  };
}
