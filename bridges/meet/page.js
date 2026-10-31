// Valor's own page file, loaded after Attendee's payloads (vendor/): the
// call process (core/call.py) plays speech through the payload's virtual
// microphone, stops it at once on a barge-in, and reads where the page
// stands. Speech goes out only through the microphone: Chrome runs muted,
// so nothing plays on the Mac's speakers.
(() => {
    if (!window.ws || !window.ws.ws) return;
    // Every frame runs the payloads; the call reads the top frame's socket.
    window.ws.ws.addEventListener("open", () => {
        window.ws.sendJson({ type: "ValorHello", top: window.top === window });
    });

    let playing = null;

    // Int16 PCM, base64, at `rate`: resolves once it played out or was
    // stopped, with the seconds played.
    window.valorPlay = (b64, rate) => new Promise((resolve) => {
        const out = window.botOutputManager;
        out._createSourceAudioTrack();
        const ctx = out.audioContext;
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const pcm = new Int16Array(bytes.buffer);
        const buffer = ctx.createBuffer(1, pcm.length, rate);
        const channel = buffer.getChannelData(0);
        for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 32768;
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        source.connect(out.gainNode);
        let started = null;
        const finish = (cut) => {
            if (playing !== stop) return;
            playing = null;
            const played = started === null ? 0 : Math.min(ctx.currentTime - started, buffer.duration);
            resolve({ played, total: buffer.duration, cut });
        };
        const stop = () => {
            try { source.stop(); } catch (e) { /* never started */ }
            finish(true);
        };
        if (playing) playing();
        playing = stop;
        source.onended = () => finish(false);
        ctx.resume().then(() => {
            if (playing !== stop) return;
            started = ctx.currentTime;
            source.start();
        });
    });

    window.valorStop = () => { if (playing) playing(); };

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
