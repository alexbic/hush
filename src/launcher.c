/*
 * HUSH launcher — runs Python as a shared library inside HUSH.app process.
 * This keeps NSBundle.mainBundle() pointing to HUSH.app, which lets
 * NSStatusBar items appear correctly in macOS 14+.
 *
 * Build: clang -framework Foundation -o HUSH launcher.c
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>
#include <mach-o/dyld.h>
#include <libgen.h>
#include <sys/param.h>

/* Py_Main removed in Python 3.13 — use Py_BytesMain (char **argv) */
typedef int (*Py_BytesMain_f)(int argc, char **argv);

/* ---------- load ~/.hush_env into process environment ---------- */
static void load_env_file(void) {
    const char *home = getenv("HOME");
    if (!home) return;
    char path[MAXPATHLEN];
    snprintf(path, sizeof(path), "%s/.hush_env", home);
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[4096];
    while (fgets(line, sizeof(line), f)) {
        /* strip newline */
        char *nl = strrchr(line, '\n'); if (nl) *nl = '\0';
        /* skip empty / comments */
        if (!line[0] || line[0] == '#') continue;
        /* strip optional "export " prefix */
        char *kv = line;
        if (strncmp(kv, "export ", 7) == 0) kv += 7;
        /* split key=value */
        char *eq = strchr(kv, '=');
        if (!eq) continue;
        *eq = '\0';
        char *val = eq + 1;
        /* strip surrounding quotes */
        size_t vlen = strlen(val);
        if (vlen >= 2 && (val[0] == '"' || val[0] == '\'') && val[vlen-1] == val[0]) {
            val[vlen-1] = '\0'; val++;
        }
        setenv(kv, val, 0);  /* 0 = don't override existing env */
    }
    fclose(f);
}

/* ---------- find Python framework dylib ---------- */
static void *load_python(void) {
    /* Try stable Homebrew opt symlink first, then specific version */
    const char *candidates[] = {
        "/opt/homebrew/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/Python",
        "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/Python",
        "/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12/Python",
        "/usr/local/opt/python@3.14/Frameworks/Python.framework/Versions/3.14/Python",
        NULL
    };
    for (int i = 0; candidates[i]; i++) {
        void *lib = dlopen(candidates[i], RTLD_NOW | RTLD_GLOBAL);
        if (lib) return lib;
    }
    return NULL;
}

int main(int argc, char **argv) {
    /* 1. Source ~/.hush_env */
    load_env_file();

    /* 2. Resolve our own path to find Contents/Resources */
    char exec_raw[MAXPATHLEN];
    uint32_t exec_size = sizeof(exec_raw);
    if (_NSGetExecutablePath(exec_raw, &exec_size) != 0) {
        fprintf(stderr, "HUSH: cannot resolve executable path\n");
        return 1;
    }
    char exec_real[MAXPATHLEN];
    if (!realpath(exec_raw, exec_real)) {
        strlcpy(exec_real, exec_raw, sizeof(exec_real));
    }
    /* dirname modifies its argument on BSD — work on a copy */
    char exec_copy[MAXPATHLEN];
    strlcpy(exec_copy, exec_real, sizeof(exec_copy));
    char *macos_dir = dirname(exec_copy);

    char resources[MAXPATHLEN];
    snprintf(resources, sizeof(resources), "%s/../Resources", macos_dir);
    setenv("RESOURCEPATH", resources, 1);

    char script[MAXPATHLEN];
    snprintf(script, sizeof(script), "%s/main.py", resources);

    /* 3. Load Python framework */
    void *py_lib = load_python();
    if (!py_lib) {
        fprintf(stderr,
            "HUSH: Python 3.14 not found.\n"
            "Install with: brew install python@3.14\n");
        return 1;
    }

    Py_BytesMain_f py_main = (Py_BytesMain_f)dlsym(py_lib, "Py_BytesMain");
    if (!py_main) {
        fprintf(stderr, "HUSH: cannot find Py_BytesMain in Python framework\n");
        return 1;
    }

    /* 4. Log before starting Python */
    FILE *log = fopen("/tmp/hush_launcher.log", "a");
    if (log) {
        fprintf(log, "HUSH launcher: script=%s resources=%s\n", script, resources);
        fclose(log);
    }

    /* 5. Run main.py via Py_BytesMain.
     * argc=2: argv[0]="python3" (program name), argv[1]=script.
     * With argc=1, some Python versions interpret argv[0] as program name
     * and enter interactive mode instead of running the script. */
    char *py_argv[] = { "python3", script, NULL };
    int ret = py_main(2, py_argv);

    log = fopen("/tmp/hush_launcher.log", "a");
    if (log) {
        fprintf(log, "HUSH launcher: Py_BytesMain returned %d\n", ret);
        fclose(log);
    }
    return ret;
}
