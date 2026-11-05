// Valor's own page file, loaded after Attendee's payloads (vendor/): the
// call process (core/call.py) streams Gemini Live's speech through the
// payload's virtual microphone, stops it at once when it is cut, and reads
// where the page stands. Speech goes out only through the microphone: Chrome runs muted,
// so nothing plays on the Mac's speakers.
(() => {
    if (!window.ws || !window.ws.ws) return;
    // Every frame runs the payloads; the call reads the top frame's socket.
    window.ws.ws.addEventListener("open", () => {
        window.ws.sendJson({ type: "ValorHello", top: window.top === window });
    });

    // Each media connection's state, for the call's log: a call that joined
    // and saw who was in it but never heard anyone (the Cowboy, 2026-11-05)
    // got none of what Meet sends over these.
    const Made = window.RTCPeerConnection;
    let made = 0;
    window.RTCPeerConnection = function (...args) {
        const pc = new Made(...args);
        const n = ++made;
        pc.addEventListener("connectionstatechange", () => {
            window.ws.sendJson({ type: "ValorMedia", connection: n, state: pc.connectionState });
        });
        return pc;
    };
    window.RTCPeerConnection.prototype = Made.prototype;

    // Gemini Live's speech, a chunk at a time: each Int16 PCM chunk
    // (base64, at `rate`) starts where the last one ends, so a turn plays
    // without gaps. `fresh` starts a new turn's count of seconds played.
    // Resolves with the seconds still queued to play.
    let sources = [];
    let next = 0;
    window.valorStream = (b64, rate, fresh) => {
        const out = window.botOutputManager;
        out._createSourceAudioTrack();
        const ctx = out.audioContext;
        if (fresh) sources = [];
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const pcm = new Int16Array(bytes.buffer);
        const buffer = ctx.createBuffer(1, pcm.length, rate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(out.gainNode);
        return ctx.resume().then(() => {
            const at = Math.max(ctx.currentTime, next);
            source.start(at);
            next = at + buffer.duration;
            sources.push({ source, at, duration: buffer.duration });
            return next - ctx.currentTime;
        });
    };

    // Stops the turn at once, everything queued with it; the seconds of it
    // that played.
    window.valorStop = () => {
        const ctx = window.botOutputManager && window.botOutputManager.audioContext;
        if (!ctx) return 0;
        let played = 0;
        for (const s of sources) {
            played += Math.min(Math.max(ctx.currentTime - s.at, 0), s.duration);
            try { s.source.stop(); } catch (e) { /* never started */ }
        }
        sources = [];
        next = 0;
        return played;
    };

    // Where the page stands, and who is in the call.
    window.valorState = () => {
        const text = document.body ? document.body.innerText : "";
        const has = (s) => text.includes(s);
        const users = window.userManager;
        let status = "joining";
        if (has("You've been removed from the meeting")) status = "removed";
        else if (has("denied your request to join") || has("to your request to join the call")) status = "denied";
        else if (has("The call ended because everyone left") || has("ended the meeting for everyone")) status = "ended";
        else if (has("You left the meeting") || has("You can't join this video call")) status = "ended";
        else if (users && users.currentUserIsInWaitingRoom()) status = "lobby";
        else if (document.querySelector('button[aria-label="Leave call"]')) status = "in";
        else if (has("Asking to be let in")) status = "lobby";
        const people = users ? users.getCurrentUsersInMeeting().filter((u) => !u.parentDeviceId) : [];
        return {
            status,
            people: people.map((u) => ({ id: u.deviceId, name: u.fullName || u.displayName || "", me: !!u.isCurrentUser })),
        };
    };
})();
