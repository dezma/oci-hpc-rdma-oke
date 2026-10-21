FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    perftest rdma-core ibverbs-utils libibverbs1 ibverbs-providers \
    iproute2 iputils-ping ethtool pciutils procps util-linux jq curl \
    python3 python3-mpi4py openmpi-bin libucx0 ucx-utils openssh-server openssh-client numactl passwd ca-certificates coreutils \
    && rm -rf /var/lib/apt/lists/*
# The same immutable image is used in two reviewed modes:
# 1. non-privileged Dranet IPvlan RDMA validation pods; and
# 2. ephemeral fixed-profile node diagnostics with a read-only /host mount.
# Host RDMA drivers remain owned by the OKE worker image and NVIDIA/OCI agents.
CMD ["sleep", "infinity"]
