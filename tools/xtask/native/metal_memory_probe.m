#import <Metal/Metal.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

static const uint64_t magic = UINT64_C(0x534b495050594d32);

static uint64_t allocated_bytes(void) {
    @autoreleasepool {
        uint64_t total = 0;
        for (id<MTLDevice> device in MTLCopyAllDevices()) {
            uint64_t bytes = device.currentAllocatedSize;
            if (UINT64_MAX - total < bytes) {
                return 0;
            }
            total += bytes;
        }
        return total;
    }
}

static void *serve(void *argument) {
    int listener = (int)(intptr_t)argument;
    for (;;) {
        int connection = accept(listener, NULL, NULL);
        if (connection < 0) {
            break;
        }
        int enabled = 1;
        struct timeval timeout = { .tv_sec = 2 };
        char request;
        if (setsockopt(connection, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0 &&
            setsockopt(connection, SOL_SOCKET, SO_NOSIGPIPE, &enabled, sizeof(enabled)) == 0 &&
            read(connection, &request, 1) == 1 && request == '?') {
            uint64_t reply[] = { magic, (uint64_t)getpid(), allocated_bytes() };
            (void)write(connection, reply, sizeof(reply));
        }
        close(connection);
    }
    close(listener);
    return NULL;
}

__attribute__((constructor))
static void start_probe(void) {
    const char *path = getenv("SKIPPY_METAL_MEMORY_SOCKET");
    struct sockaddr_un address = { .sun_family = AF_UNIX, .sun_len = sizeof(address) };
    if (path == NULL || *path == '\0' || strlen(path) >= sizeof(address.sun_path)) {
        return;
    }
    strlcpy(address.sun_path, path, sizeof(address.sun_path));
    int listener = socket(AF_UNIX, SOCK_STREAM, 0);
    if (listener < 0) {
        return;
    }
    pthread_t thread;
    if (bind(listener, (struct sockaddr *)&address, sizeof(address)) != 0 ||
        listen(listener, 1) != 0 ||
        pthread_create(&thread, NULL, serve, (void *)(intptr_t)listener) != 0) {
        close(listener);
        return;
    }
    pthread_detach(thread);
}
