FROM ghcr.io/translatorsri/renci-python-image:3.11.5

# Add image info
LABEL org.opencontainers.image.source /github.com/TranslatorSRI/TestHarness

WORKDIR /app

# set up requirements
COPY requirements.txt .
COPY requirements-runners.txt .
RUN pip install -r requirements.txt
RUN pip install -r requirements-runners.txt

# set up source
COPY . .

# make sure all is writeable for the nru USER later on
RUN chmod -R 777 .

RUN pip install .

# switch to the non-root user (nru). defined in the base image
USER nru
