function attendanceApp() {
  return {
    tab: 'verify',
    busy: false,
    setupRequired: false,
    adminAuthenticated: false,
    streams: { register: null, verify: null },
    cameraActive: { register: false, verify: false },
    config: {
      test_mode: true,
      geolocation_enabled: false,
      anti_spoofing_enabled: false,
      radius_m: 100,
      max_accuracy_m: 80,
      office_lat: 18.5204,
      office_lng: 73.8567,
      face_engine_ready: true,
    },
    setup: {
      admin_password: '',
      admin_password_confirm: '',
      test_mode: true,
      enable_geolocation: false,
      enable_anti_spoofing: false,
      office_lat: 18.5204,
      office_lng: 73.8567,
      geofence_radius_m: 100,
      max_gps_accuracy_m: 80,
    },
    admin: { password: '', current_password: '', new_password: '', new_password_confirm: '' },
    settings: {
      test_mode: true,
      enable_geolocation: false,
      enable_anti_spoofing: false,
      office_lat: 18.5204,
      office_lng: 73.8567,
      geofence_radius_m: 100,
      max_gps_accuracy_m: 80,
    },
    register: { user_id: '', name: '' },
    verify: { user_id: '' },
    history: [],
    message: { type: 'info', title: '', text: '', metrics: '' },

    apiUrl(path) {
      const base = new URL(window.location.href);
      base.search = '';
      base.hash = '';
      if (!base.pathname.endsWith('/')) base.pathname += '/';
      return new URL(path.replace(/^\//, ''), base).toString();
    },

    async init() {
      await this.refreshConfig();
      this.$nextTick(() => {
        if (window.gsap) gsap.from('.topbar, .notice, .tabs', { opacity: 0, y: -12, stagger: 0.08, duration: 0.5 });
      });
    },

    applyConfig(data) {
      this.config = data;
      this.setupRequired = !!data.setup_required;
      this.adminAuthenticated = !!data.admin_authenticated;
      const values = {
        test_mode: !!data.test_mode,
        enable_geolocation: !!data.geolocation_enabled,
        enable_anti_spoofing: !!data.anti_spoofing_enabled,
        office_lat: Number(data.office_lat),
        office_lng: Number(data.office_lng),
        geofence_radius_m: Number(data.radius_m),
        max_gps_accuracy_m: Number(data.max_accuracy_m),
      };
      Object.assign(this.settings, values);
      if (this.setupRequired) Object.assign(this.setup, values);
    },

    async refreshConfig() {
      try {
        const res = await fetch(this.apiUrl('api/config'), { credentials: 'same-origin' });
        const data = await res.json();
        this.applyConfig(data);
        if (this.adminAuthenticated && this.tab === 'history') await this.loadHistory();
      } catch (_) {
        this.showMessage('error', 'Server unavailable', 'Could not load application configuration.');
      }
    },

    async completeSetup() {
      if (this.setup.admin_password.length < 10) {
        return this.showMessage('error', 'Password too short', 'Use at least 10 characters for the administrator password.');
      }
      if (this.setup.admin_password !== this.setup.admin_password_confirm) {
        return this.showMessage('error', 'Passwords do not match', 'Enter the same administrator password twice.');
      }
      this.busy = true;
      try {
        const payload = { ...this.setup };
        delete payload.admin_password_confirm;
        const data = await this.postJSON('api/setup', payload);
        if (!data.ok) throw new Error(data.message);
        this.applyConfig(data.config);
        this.setup.admin_password = '';
        this.setup.admin_password_confirm = '';
        this.tab = 'admin';
        this.showMessage('success', 'Setup complete', 'The application is configured and the administrator session is active.');
      } catch (err) {
        this.showMessage('error', 'Setup failed', err.message);
      } finally {
        this.busy = false;
      }
    },

    async loginAdmin() {
      if (!this.admin.password) return this.showMessage('error', 'Password required', 'Enter the administrator password.');
      this.busy = true;
      try {
        const data = await this.postJSON('api/admin/login', { password: this.admin.password });
        if (!data.ok) throw new Error(data.message);
        this.admin.password = '';
        this.applyConfig(data.config);
        this.showMessage('success', 'Administrator signed in', 'Enrollment, history and settings are now available.');
      } catch (err) {
        this.showMessage('error', 'Sign-in failed', err.message);
      } finally {
        this.busy = false;
      }
    },

    async logoutAdmin() {
      this.stopCamera('register');
      await this.postJSON('api/admin/logout', {});
      this.adminAuthenticated = false;
      this.history = [];
      this.tab = 'verify';
      await this.refreshConfig();
      this.showMessage('info', 'Signed out', 'Administrator controls are locked.');
    },

    async saveSettings() {
      this.busy = true;
      try {
        const data = await this.postJSON('api/admin/settings', this.settings);
        if (!data.ok) throw new Error(data.message);
        this.applyConfig(data.config);
        this.showMessage('success', 'Settings saved', 'Changes take effect immediately.');
      } catch (err) {
        this.handleAdminError(err, 'Settings failed');
      } finally {
        this.busy = false;
      }
    },

    async changePassword() {
      if (this.admin.new_password.length < 10) {
        return this.showMessage('error', 'Password too short', 'Use at least 10 characters for the new password.');
      }
      if (this.admin.new_password !== this.admin.new_password_confirm) {
        return this.showMessage('error', 'Passwords do not match', 'Enter the same new password twice.');
      }
      this.busy = true;
      try {
        const data = await this.postJSON('api/admin/password', {
          current_password: this.admin.current_password,
          new_password: this.admin.new_password,
        });
        if (!data.ok) throw new Error(data.message);
        this.admin.current_password = '';
        this.admin.new_password = '';
        this.admin.new_password_confirm = '';
        this.showMessage('success', 'Password updated', data.message);
      } catch (err) {
        this.handleAdminError(err, 'Password update failed');
      } finally {
        this.busy = false;
      }
    },

    handleAdminError(err, title) {
      if (err.status === 401) {
        this.adminAuthenticated = false;
        this.tab = 'admin';
      }
      this.showMessage('error', title, err.message);
    },

    async startCamera(kind) {
      this.stopCamera(kind);
      if (!navigator.mediaDevices?.getUserMedia) {
        return this.showMessage('error', 'Camera unavailable', 'This browser does not expose secure camera access. Use HTTPS in a supported browser.');
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'user', width: { ideal: 960 }, height: { ideal: 720 } },
          audio: false,
        });
        this.streams[kind] = stream;
        this.cameraActive[kind] = true;
        this.$refs[`${kind}Video`].srcObject = stream;
        this.showMessage('info', 'Camera ready', 'Center one face in the frame, then capture.');
      } catch (_) {
        this.showMessage('error', 'Camera blocked', 'Allow camera permission and open the application over HTTPS.');
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
      ctx.save();
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      ctx.restore();
      return canvas.toDataURL('image/jpeg', 0.9);
    },

    async registerFace() {
      if (!this.adminAuthenticated) return this.showMessage('error', 'Administrator required', 'Sign in before enrolling a person.');
      if (!this.register.user_id || !this.register.name) {
        return this.showMessage('error', 'Missing details', 'Enter both User ID and name.');
      }
      let image;
      try { image = this.capture('register'); }
      catch (err) { return this.showMessage('error', 'Camera not ready', err.message); }

      this.busy = true;
      try {
        const data = await this.postJSON('api/register', { ...this.register, image });
        if (!data.ok) throw new Error(data.message);
        this.showMessage('success', 'Enrollment complete', data.message);
        this.verify.user_id = this.register.user_id;
        this.register = { user_id: '', name: '' };
        this.stopCamera('register');
        this.tab = 'verify';
      } catch (err) {
        this.handleAdminError(err, 'Registration failed');
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
        const data = await this.postJSON('api/verify', {
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
      if (!this.adminAuthenticated) return;
      try {
        const res = await fetch(this.apiUrl('api/attendance'), { credentials: 'same-origin' });
        const data = await res.json();
        if (res.status === 401) {
          this.adminAuthenticated = false;
          this.tab = 'admin';
          return;
        }
        this.history = data.rows || [];
      } catch (_) {
        this.history = [];
      }
    },

    async postJSON(path, payload) {
      const res = await fetch(this.apiUrl(path), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({ ok: false, message: 'Invalid server response.' }));
      if (!res.ok && !data.message) data.message = `Request failed (${res.status})`;
      if (!res.ok) {
        const err = new Error(data.message || `Request failed (${res.status})`);
        err.status = res.status;
        throw err;
      }
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
