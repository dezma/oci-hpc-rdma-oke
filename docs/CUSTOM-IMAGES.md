# Create the Ubuntu HPC custom image

Complete this step before provisioning workers. Importing a supplied `.oci` image creates a custom image in your OCI tenancy; you do not need to build an operating system image yourself.

These are **CPU HPC images (no GPU)** for workers. The operator uses its separately selected Oracle Linux image. The container tools image is built later on the operator using the [registry guide](IMAGE-PULL-SECRET.md).

## 1. Choose an image

| Ubuntu release | Download / import URL | Resource Manager Ubuntu release |
| --- | --- | --- |
| Ubuntu 24.04 HPC (no GPU) | [Download .oci image](https://idxzjcdglx2s.objectstorage.eu-frankfurt-1.oci.customer-oci.com/p/ZPwaXxeidz3BPrdzEQt_9r-CnrWWOUR_DQ7sB60j3-lqq5bXhLL0LF43J-QYGaua/n/idxzjcdglx2s/b/oci-hpc-image-builds/o/images/2026.07.16/Canonical-Ubuntu-24.04-2026.02.28-0-KERNEL-ORACLE-6.8-DOCA-OFED-3.3.0-2026.07.16-0.oci) | `ubuntu-noble` |
| Ubuntu 22.04 HPC (no GPU) | [Download .oci image](https://idxzjcdglx2s.objectstorage.eu-frankfurt-1.oci.customer-oci.com/p/ZPwaXxeidz3BPrdzEQt_9r-CnrWWOUR_DQ7sB60j3-lqq5bXhLL0LF43J-QYGaua/n/idxzjcdglx2s/b/oci-hpc-image-builds/o/images/2026.07.16/Canonical-Ubuntu-22.04-2026.02.28-0-KERNEL-ORACLE-6.8-DOCA-OFED-3.3.0-2026.07.16-0.oci) | `ubuntu-jammy` |

You can copy a link directly into OCI's import form; downloading the file to your laptop first is optional. These are pre-authenticated Object Storage links. Anyone with a link can access its target while it remains valid; an expired or revoked link requires a replacement from the image owner.

## 2. Import into OCI

1. In OCI Console, select your **deployment region**, then open **Compute → Custom Images**.
2. Select your project compartment and click **Import image**. Your account needs permission to create custom images there.
3. Give the image a clear name, for example `Ubuntu-24.04-HPC-CPU-2026.07.16`.
4. Select **Linux** as the operating system and **Import from an Object Storage URL** as the source. Paste the complete matching link above.
5. Choose image type **OCI**. Launch mode is determined from the `.oci` metadata; do not force another format.
6. Start the import. Monitor its work request and wait until the image is **Available**.
7. Open the imported image and copy its **OCID**.

Oracle reference: [Importing Custom Images](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/custom-images-import.htm).

## 3. Use it in this project

In the Resource Manager stack form:

| Field | Value |
| --- | --- |
| Ubuntu HPC custom image OCID | OCID of the imported, Available image (`hpc_custom_image_ocid`) |
| Ubuntu release used by the HPC custom image | `ubuntu-noble` for 24.04 or `ubuntu-jammy` for 22.04 (`ubuntu_release`) |
| Kubernetes version | A full OKE patch version offered in your region and supported by the project's compatibility mapping |
| Worker shape | Your compatible HPC shape; the reference profile uses `BM.Optimized3.36` |

Use the image imported into the deployment region. The Frankfurt download location does not supply an image OCID for your tenancy. If deploying in another region, import there and record that region's image OCID.

Keep this OCID pinned for subsequent scaling. Review the Terraform Plan before Apply; a worker-count change should not unintentionally change images. After provisioning, run host readiness and cross-node RDMA validation.

Return to [provisioning](../README.md#2-build-the-resource-manager-zip).


