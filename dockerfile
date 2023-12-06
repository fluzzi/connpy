# Use the official python image

FROM python:3.11-alpine as connpy-app

# Set the entrypoint
# Set the working directory
WORKDIR /app

# Install any additional dependencies
RUN apk update && apk add --no-cache openssh fzf fzf-tmux ncurses bash
RUN pip3 install connpy
RUN connpy config --configfolder /app

#AUTH
RUN ssh-keygen -A
RUN mkdir /root/.ssh && \
    chmod 700 /root/.ssh


#Set the entrypoint
ENTRYPOINT ["connpy"]
