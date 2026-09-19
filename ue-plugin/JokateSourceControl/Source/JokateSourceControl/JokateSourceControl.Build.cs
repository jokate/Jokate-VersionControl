// Jokate revision control plugin for Unreal Engine.

using UnrealBuildTool;

public class JokateSourceControl : ModuleRules
{
	public JokateSourceControl(ReadOnlyTargetRules Target) : base(Target)
	{
		PrivateDependencyModuleNames.AddRange(
			new string[]
			{
				"Core",
				"CoreUObject",
				"Slate",
				"SlateCore",
				"InputCore",
				"SourceControl",
				"Projects",
				"ToolMenus",
				"HTTP",
				"Json"
			}
		);

		if (Target.bBuildEditor)
		{
			PrivateDependencyModuleNames.AddRange(
				new string[]
				{
					"UnrealEd"
				}
			);
		}
	}
}
