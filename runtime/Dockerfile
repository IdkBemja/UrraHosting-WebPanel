# Reference base image (plan.md section 5.2). NOT built or used directly by
# the platform - applications always supply and are built from their own
# Dockerfile (plan.md section 3.2) - this is documentation teams can `FROM`
# in their own image (or copy the two conventions below) to start from the
# platform's non-root/fixed-port contract instead of reinventing it:
#
#   * A fixed, unprivileged, non-root user (uid/gid 10001) owns nothing
#     outside its own working directory - it never runs as root even
#     transiently, and gets no HOME-based caches with unpredictable perms.
#   * runtime/entrypoint.sh: refuses to start without APP_PORT and execs
#     the real command as the non-root user.
#
# See runtime/entrypoint.sh and config/runtime_profiles.py for the profile
# policy (allowed base images per language, default health-check path) that
# dockerfile_validator.py enforces against the application's own Dockerfile.
FROM debian:12-slim

RUN groupadd -g 10001 app \
    && useradd -u 10001 -g app -m -d /home/app -s /usr/sbin/nologin app \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu tini \
    && rm -rf /var/lib/apt/lists/*

COPY runtime/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

WORKDIR /app
USER app

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
