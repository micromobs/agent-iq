import * as React from 'react';
import * as ReactDOM from 'react-dom';

import { BaseCopilotComponent } from '@microsoft/sp-copilot-component';
import type { SPCopilotDisplayMode } from '@microsoft/sp-copilot-component';

import EmployeeOperations from './components/EmployeeOperations';
import type { IEmployeeOperationsProps } from './components/IEmployeeOperationsProps';
import {
  ALL_EMPLOYEE_EXPERIENCES,
  getEmployeeExperience
} from './data/employeeDemoData';
import type { IEmployeeOperationsCopilotComponentProperties } from './EmployeeOperationsCopilotComponentProperties';

export default class EmployeeOperationsCopilotComponent extends BaseCopilotComponent<IEmployeeOperationsCopilotComponentProperties> {
  protected render(): void {
    const experience = this.properties.experience ?? getEmployeeExperience(this.properties.scenarioId);
    const props: IEmployeeOperationsProps = {
      experience,
      allExperiences: ALL_EMPLOYEE_EXPERIENCES,
      userDisplayName: 'Employee',
      siteTitle: 'Microsoft 365 Copilot',
      serviceNotices: [],
      initialView: this.properties.initialView,
      hostContext: this.hostContext,
      bridge: this.context.copilotBridge,
      onRequestDisplayMode: async (mode: SPCopilotDisplayMode) => {
        await this.requestDisplayModeAsync(mode);
      },
      onRequestSizeChange: async (width: number, height: number) => {
        await this.requestSizeChangeAsync(width, height);
      },
      targetDocument: this.context.domElement.ownerDocument
    };

    ReactDOM.render(React.createElement(EmployeeOperations, props), this.context.domElement);
  }

  protected async onTeardown(): Promise<void> {
    ReactDOM.unmountComponentAtNode(this.context.domElement);
  }
}
