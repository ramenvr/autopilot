// tools/net_smoke.py's test game: the template's game mode, with the test game's player controller, and a training
// dummy spawned in front of the player start (on the server: the game mode exists only there).

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "TP_ThirdPersonGameMode.generated.h"

UCLASS(minimalapi)
class ATP_ThirdPersonGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	ATP_ThirdPersonGameMode();

protected:
	virtual void BeginPlay() override;
};
