# Azure CDKTF Repository

This repository contains the cdktf for deploying code to Azure Sandbox environment

# Repository Structure and Overview
 The CDK code is organized under the infra folder, which contains three main libraries: bin, lib, and services.
- The services folder is where the actual functionality within an app's service or function is written in Python code.
- The bin and lib folders refer to CDK and the logic and naming for the resources.
- The starting point for the code is infra.ts, where specific services are imported and new instances are created.
- Each service has a corresponding .ts file in the lib folder, which is then imported into infra.ts to create new instances of the service.

```
├───.github
│   ├───ISSUE_TEMPLATE
│   └───workflows
├───infra
│   ├───bin
│   └───lib
└───service
    └───functions
```

# Steps to create Resources
1. For each services, create a service speicifc .ts file under infra/library
2. Import this library and initialize this service class in infra.ts
3. Update package.json with cdktf dependency for that service under dependancy
4. Create a pull request
5. comment the PR with specific text/format to trigger individual or shared resource deployment pipeline

For example, for the app service, the app service file is imported from the cdktf library, and a new app service environment is created with specific naming conventions. This process is repeated for each service, ensuring that each service has its own specific file in the lib folder

# GitHub Actions and Deployment:
The workflow file in the .github folder contains the YAML configuration that defines the steps for deployment, including a diff check and ensuring files in lib meet standards before deploying services.
- Triggering Actions: GitHub actions are triggered by a specific command in a pull request. This command initiates the workflow defined in the .github folder, which contains the YAML configuration for deployment.

```
# Add the following comment if you are deploying to your speicifc resource group
/cdktf:sbx,1,cc1
```

```
# if you are deploying to a shared resource group ,Add label CloudEng in PR and CE approval is mandatory to trigger the pipeline
/cdktf:shr,1,cc1
```
- Workflow Steps: The workflow file includes steps such as a diff check to ensure that no base parameters set by CE are changed. It also checks that the files in the lib folder meet the required standards before proceeding with the deployment of services.
- Deployment Process: The deployment process involves running the infra.ts file, which imports and sets up the necessary services. The YAML file defines the events that trigger the deployment and the specific actions to be taken during the deployment process