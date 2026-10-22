# Build .build/tailcat-bridge with the exact Go/module lock before building this image.
FROM scratch
COPY --chmod=0555 .build/tailcat-bridge /bridge
USER 1000:1000
ENTRYPOINT ["/bridge"]
