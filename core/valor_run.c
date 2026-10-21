/* valor-run PROGRAM [ARGS...]: Valor.app's executable (core/permissions.py).
 * Runs PROGRAM as its child with the same environment, working directory and
 * descriptors, forwards TERM, INT and HUP to it, and exits as it exited. The
 * child and everything it starts are attributed to Valor.app by macOS
 * privacy checks. */
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t child;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s program [args...]\n", argv[0]);
        return 64;
    }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);
    int err = posix_spawnp(&child, argv[1], NULL, NULL, argv + 1, environ);
    if (err) {
        fprintf(stderr, "%s: %s\n", argv[1], strerror(err));
        return 127;
    }
    int status;
    while (waitpid(child, &status, 0) < 0) {
    }
    if (WIFSIGNALED(status)) {
        signal(WTERMSIG(status), SIG_DFL);
        raise(WTERMSIG(status));
    }
    return WEXITSTATUS(status);
}
